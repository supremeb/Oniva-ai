"""Log redaction for /internal: Authorization headers and apiKey fields never reach logs."""
import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[^\s\"',}]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([\"']?api_?key[\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,}]+)"), r"\1\"[REDACTED]\""),
]


def redact(text: str) -> str:
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # never let redaction break logging
            pass
        return True


def install_redaction() -> None:
    """Attach the filter to every root/uvicorn handler (idempotent)."""
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())
