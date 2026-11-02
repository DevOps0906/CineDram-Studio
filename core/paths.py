"""작업 루트 안으로 경로 제한 (docs/16 ADR-003).

사용자 입력 경로는 항상 '루트 기준 상대 경로'여야 한다. 절대 경로·드라이브·UNC·`\\?\\`·
`C:상대`·ADS(`:`)·`..` 탈출·Windows 예약 이름·끝 점/공백·심볼릭/junction 실경로 탈출을 거부한다.

주의(TOCTOU): 검사와 사용 사이에 경로가 바뀔 수 있다. 반환된 실경로(Path)만 쓰고,
검사 직후 바로 사용한다. 반환값을 저장해 두었다가 나중에 다시 쓰지 않는다.
"""
import os
import re
from pathlib import Path

SOURCE_DIR = "원본"
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$", re.IGNORECASE)


class PathRejected(ValueError):
    pass


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _inside(base, target):
    b, t = _norm(base), _norm(target)
    return t == b or t.startswith(b + os.sep)


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
