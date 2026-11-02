"""서버 공용 의존성: 템플릿 환경, DB 연결, 폼 파서(C10), 아이콘·메뉴 데이터."""
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from markupsafe import Markup, escape
from starlette.templating import Jinja2Templates

from server.security import MAX_BODY_BYTES

SERVER_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SERVER_DIR / "templates"
STATIC_DIR = SERVER_DIR / "static"
MAX_FORM_FIELDS = 100
FORM_TYPE = "application/x-www-form-urlencoded"

# ───────── 폼 파서 ─────────


class Form:
    """urlencoded 본문을 파싱한 결과. 값은 문자열 목록(반복 필드 보존)."""

    def __init__(self, data):
        self._d = data

    def get(self, name, default=""):
        vals = self._d.get(name)
        return vals[0] if vals else default

    def getlist(self, name):
        return list(self._d.get(name, []))

    def __contains__(self, name):
        return name in self._d

    def keys(self):
        return self._d.keys()


def parse_form_body(body: bytes) -> Form:
    """바이트 → Form. 잘못된 UTF-8·필드 과다는 400."""
    try:
        text = body.decode("utf-8")
        data = parse_qs(text, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS,
                        encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="폼 데이터를 읽을 수 없습니다")
    return Form(data)


async def read_form(request: Request, max_body: int = MAX_BODY_BYTES) -> Form:
    """POST 폼을 읽는다. python-multipart 없이 urlencoded만 받는다(상한은 보안 미들웨어와 같은 값)."""
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != FORM_TYPE:
        raise HTTPException(status_code=415, detail="지원하지 않는 형식입니다")
    total = 0
    chunks = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_body:
            raise HTTPException(status_code=413, detail="본문이 너무 큽니다")
        chunks.append(chunk)
    return parse_form_body(b"".join(chunks))


# ───────── DB ─────────


def open_db(db_path):
    """기존 DB만 연다(없으면 만들지 않음). 행은 sqlite3.Row, 트랜잭션은 호출부가 직접 연다."""
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # ADR-004: 워커와 동시 접근
    return conn


def get_db(request: Request):
    try:
        conn = open_db(request.app.state.db_path)
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="데이터베이스를 열 수 없습니다")
    try:
        yield conn
    finally:
        conn.close()


# ───────── 템플릿 ─────────

_ICON_PATHS = {
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    "home": '<path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    "zap": '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    "layers": '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>',
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
    "archive": '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4M12 16h.01"/>',
    "tv": '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9l5 3-5 3z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
    "close": '<path d="M18 6L6 18M6 6l12 12"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "warn": '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "circlecheck": '<circle cx="12" cy="12" r="9"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "scaneye": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="1.5"/><path d="M18.5 12s-2.5 4.5-6.5 4.5S5.5 12 5.5 12 8 7.5 12 7.5s6.5 4.5 6.5 4.5z"/>',
    "filex": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9.5 12.5l5 5M14.5 12.5l-5 5"/>',
    "disk": '<path d="M22 12H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/><path d="M6 16h.01M10 16h.01"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "play": '<path d="M6 4l14 8-14 8z"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
}


def icon(name, cls=""):
    """Lucide 모양 인라인 SVG. 이름은 허용 목록뿐이고 cls 는 이스케이프한다."""
    path = _ICON_PATHS.get(name, "")
    return Markup(f'<svg class="ico {escape(cls)}" viewBox="0 0 24 24" aria-hidden="true">{path}</svg>')


# 사이드바 정보구조: (그룹, [(id, href|None, 아이콘, 이름)]). href None = 준비 중(비활성).
NAV_GROUPS = (
    ("홈", (("dashboard", None, "home", "홈"),)),
    ("제작", (("oneclick", "/make/oneclick", "zap", "원클릭 제작"), ("make", "/make", "plus", "만들기"),
            ("link", None, "link", "링크로 만들기"), ("queue", "/queue", "layers", "대기 큐"),
            ("status", "/status", "activity", "제작 현황"))),
    ("검수", (("inbox", "/inbox", "inbox", "승인함"),)),
    ("소재", (("radar", None, "radar", "레이더"),)),
    ("성과", (("archive", None, "archive", "영상 보관함"), ("report", None, "activity", "채널 성적표"))),
    ("권리", (("risk", None, "shield", "리스크 기록"),)),
    ("설정", (("channels", None, "tv", "채널 관리"), ("settings", None, "gear", "환경설정"))),
)
# 모바일 하단 탭(활성 링크만)
TAB_ITEMS = (("make", "/make", "plus", "만들기"), ("status", "/status", "activity", "제작 현황"),
             ("inbox", "/inbox", "inbox", "승인함"))

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(icon=icon, NAV_GROUPS=NAV_GROUPS, TAB_ITEMS=TAB_ITEMS)
