"""작업 루트 안으로 경로 제한 (docs/16 ADR-003).

사용자 입력 경로는 항상 '루트 기준 상대 경로'여야 한다. 절대 경로·드라이브·UNC·`\\?\\`·
`C:상대`·ADS(`:`)·`..` 탈출·Windows 예약 이름·끝 점/공백·심볼릭/junction 실경로 탈출을 거부한다.

주의(TOCTOU): 검사와 사용 사이에 경로가 바뀔 수 있다. 반환된 실경로(Path)만 쓰고,
검사 직후 바로 사용한다. 반환값을 저장해 두었다가 나중에 다시 쓰지 않는다.

DB 경로: 환경 변수 CINEDRAM_DB(서버·워커 공통, 미설정이면 저장소 var/studio.db).
작업 루트: 환경 변수 CINEDRAM_ROOTS(os.pathsep 구분, 절대 경로만, 첫 값이 기본 루트).
설정하면 resolve_in 의 base 자체가 그 루트들 중 하나 안에 있어야 한다. 미설정이면 저장소 폴더가
기본 루트이고 base 제한은 없다(하위 호환).
"""
import os
import re
from pathlib import Path

SOURCE_DIR = "원본"
ROOTS_ENV = "CINEDRAM_ROOTS"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DB_ENV = "CINEDRAM_DB"
DEFAULT_DB_PATH = DEFAULT_ROOT / "var" / "studio.db"
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$", re.IGNORECASE)


class PathRejected(ValueError):
    pass


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _inside(base, target):
    b, t = _norm(base), _norm(target)
    return t == b or t.startswith(b + os.sep)


def configured_roots():
    """CINEDRAM_ROOTS 의 실경로 목록. 미설정·빈 값이면 []. 절대 경로가 아닌 값이 있으면 PathRejected."""
    raw = os.environ.get(ROOTS_ENV) or ""
    roots = []
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        if not os.path.isabs(item):
            raise PathRejected(f"{ROOTS_ENV} 에는 절대 경로만 쓴다")
        roots.append(Path(os.path.realpath(item)))
    return roots


def work_roots():
    """허용 작업 루트 목록. 미설정이면 [저장소 폴더]."""
    return configured_roots() or [DEFAULT_ROOT]


def default_root():
    """기본 작업 루트(CINEDRAM_ROOTS 첫 값, 없으면 저장소 폴더)."""
    return work_roots()[0]


def db_path_from_env(default=DEFAULT_DB_PATH):
    """CINEDRAM_DB 가 있으면 그 경로, 없으면 default(하위 호환). 빈 값은 미설정, 절대 경로가 아니면 PathRejected."""
    raw = (os.environ.get(DB_ENV) or "").strip()
    if not raw:
        return Path(default)
    if not os.path.isabs(raw):
        raise PathRejected(f"{DB_ENV} 에는 절대 경로만 쓴다")
    return Path(raw)


def _check_component(part):
    if part in ("", "."):
        return
    if part.endswith(".") or part.endswith(" "):
        raise PathRejected("구성요소가 점·공백으로 끝남")
    if _RESERVED.match(part.split(".")[0].rstrip(" ")):
        raise PathRejected("Windows 예약 이름 불가")


def resolve_in(base, rel):
    """base 아래의 실경로(Path)를 돌려준다. 벗어나면 PathRejected. 존재하지 않아도 된다."""
    if not isinstance(rel, (str, os.PathLike)):
        raise PathRejected("경로 형식 오류")
    rel = os.fspath(rel)
    if not rel or "\x00" in rel:
        raise PathRejected("빈 경로 또는 NUL")
    if ":" in rel:
        raise PathRejected("드라이브·ADS(':') 불가")
    if rel[0] in ("/", "\\") or rel.startswith("\\\\"):
        raise PathRejected("절대·UNC 경로 불가")
    if os.path.isabs(rel):
        raise PathRejected("절대 경로 불가")
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts:
        raise PathRejected("'..' 불가")
    for part in parts:
        _check_component(part)
    base_real = Path(os.path.realpath(base))
    roots = configured_roots()
    if roots and not any(_inside(r, base_real) for r in roots):
        raise PathRejected(f"{ROOTS_ENV} 밖의 기준 경로")
    target = Path(os.path.realpath(base_real / rel))
    if not _inside(base_real, target):
        raise PathRejected("루트 밖으로 나가는 경로(심볼릭/junction 포함)")
    return target


def resolve_source(root, rel):
    """작업 루트의 `원본/` 안으로 제한. 반환한 실경로만 바로 사용(TOCTOU)."""
    try:
        src = resolve_in(root, SOURCE_DIR)
    except PathRejected as e:
        raise PathRejected(f"'{SOURCE_DIR}' 폴더 자체가 작업 루트 밖을 가리킴(junction/심볼릭): {e}") from e
    return resolve_in(src, rel)
