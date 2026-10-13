from __future__ import annotations

import re


_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]*)?\b")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+")


def redact(value: object, explicit_secret: str | None = None) -> str:
    text = str(value)
    if explicit_secret:
        text = text.replace(explicit_secret, "<redacted>")
    text = _BEARER.sub(r"\1<redacted>", text)
    return _JWT.sub("<redacted-jwt>", text)

