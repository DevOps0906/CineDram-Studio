"""로그 키 마스킹 (docs/16 ADR-003). 키·토큰처럼 보이는 값을 `***`로 바꾼다.

부착 시점에 의존하지 않도록 LogRecord 팩토리를 감싼다(`install()`): 어떤 로거·핸들러·전파 경로의
레코드든 만들어지는 순간 마스킹된다. 인자 튜플의 형태와 길이는 유지한다(uvicorn AccessFormatter 호환).
"""
import logging
import re
import traceback
from collections.abc import Mapping

MASK = "***"

_KEYS = r"(?:api[_-]?key|secret|token|password|passwd|credential|csrf)"
_RULES = [
    # Authorization: Basic xxx / Bearer xxx / 따옴표 값 전체
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*)[\"']?(?:(?:bearer|basic|token)\s+)?[^\s\"',;&]+[\"']?"),
     r"\1" + MASK),
    (re.compile(r"(?i)\b((?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{6,}"), r"\1" + MASK),
    # key=value, key: "값 전체(따옴표 끝까지)"
    (re.compile(r"(?i)(\b[\w-]*" + _KEYS + r"[\w-]*[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\"',;&]+)"),
     r"\1" + MASK),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), MASK),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), MASK),
]


def mask(text):
    out = str(text)
    for pat, repl in _RULES:
        out = pat.sub(repl, out)
    return out


def _mask_record(record):
    args = record.args
    if isinstance(args, tuple):
        new_args = tuple(mask(a) if isinstance(a, str) else a for a in args)
    elif isinstance(args, Mapping):
        new_args = {k: (mask(v) if isinstance(v, str) else v) for k, v in args.items()}
    else:
        new_args = args
    msg = record.msg if isinstance(record.msg, str) else str(record.msg)
    if not new_args:
        record.msg = mask(msg)
    else:
        try:
            formatted = msg % new_args
        except Exception:
            formatted = None
        if formatted is None:
            record.msg = mask(msg)
        else:
            masked = mask(formatted)
            if masked != formatted:  # 템플릿 쪽에 비밀이 있었다: 완성 문장으로 바꾸되 인자 개수는 유지
                if isinstance(new_args, tuple):
                    record.msg = masked.replace("%", "%%") + "%.0s" * len(new_args)
                else:
                    record.msg = masked.replace("%", "%%")
                    new_args = ()
    record.args = new_args
    if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
        record.exc_text = mask("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
    elif record.exc_text:
        record.exc_text = mask(record.exc_text)
    if record.stack_info:
        record.stack_info = mask(record.stack_info)


def install():
    """LogRecord 팩토리를 감싼다. 여러 번 불러도 한 번만."""
    current = logging.getLogRecordFactory()
    if getattr(current, "_logmask", False):
        return

    def factory(*a, **kw):
        record = current(*a, **kw)
        try:
            _mask_record(record)
        except Exception:
            record.msg, record.args = MASK, ()
        return record

    factory._logmask = True
    factory._inner = current
    logging.setLogRecordFactory(factory)


def uninstall():
    current = logging.getLogRecordFactory()
    while getattr(current, "_logmask", False):
        current = current._inner
    logging.setLogRecordFactory(current)
