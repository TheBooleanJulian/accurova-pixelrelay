"""Logging with secrets redacted.

PINs travel only in POST bodies and invitation tokens only in URL fragments,
so neither reaches access logs. This filter is a second line of defence for
anything that looks like a token, cookie or credential in a log line.
"""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    (
        re.compile(
            r"(?i)(pin|token|access_token|password|secret|apikey|authorization|cookie)([\"'=:\s]+)([^\s\"'&,;]+)"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"pr_(owner|room_[a-z0-9]+)=[^;\s]+"), r"pr_\1=[redacted]"),
    (re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[redacted-jwt]"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = message
        for pattern, repl in _PATTERNS:
            redacted = pattern.sub(repl, redacted)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


_done = False


def setup_logging(level: int = logging.INFO) -> None:
    global _done
    if _done:
        return
    _done = True
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(level)
    flt = RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "pixelrelay"):
        logger = logging.getLogger(name)
        logger.addFilter(flt)
        for h in logger.handlers:
            h.addFilter(flt)
