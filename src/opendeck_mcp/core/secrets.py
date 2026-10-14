"""Keep credentials (e.g. a Home Assistant access token) out of everything the agent sees."""

from __future__ import annotations

import math
import re
from typing import Any

REDACTED = "«redacted»"

SECRET_KEY = re.compile(
    r"(token|secret|passw|pwd|api[_-]?key|apikey|auth(?!or)|bearer|credential|cookie|session|private|"
    r"access[_-]?key|client[_-]?secret|webhook|signature)",
    re.IGNORECASE,
)
_JWT = re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")
_BEARER = re.compile(r"(?i)\bbearer\s+[\w.~+/=-]{8,}")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_URL_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:[\w-]*(?:token|key|secret|sig|auth|password)[\w-]*)=)[^&\s#\"']+")
_JSON_SECRET_PAIR = re.compile(
    r'(?i)("[\w-]*(?:token|secret|passw|api[_-]?key|auth|credential|cookie)[\w-]*"\s*:\s*)"[^"]*"')
# No "/" here: URL paths and file paths are not secrets, and splitting on "/" keeps them intact.
_LONG_RUN = re.compile(r"[A-Za-z0-9+=_-]{32,}")


def _entropy(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _looks_random(text: str) -> bool:
    """Long, high-entropy runs (API keys); words, slugs and paths have low entropy."""
    if len(text) < 32 or text.isalpha():
        return False
    has_mix = any(c.isdigit() for c in text) and any(c.isupper() for c in text) and any(c.islower() for c in text)
    return has_mix and _entropy(text) > 4.5


def redact_text(text: str) -> str:
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub("Bearer " + REDACTED, text)
    text = _URL_USERINFO.sub(lambda m: m.group(1) + REDACTED + "@", text)
    text = _URL_QUERY_SECRET.sub(lambda m: m.group(1) + REDACTED, text)
    text = _JSON_SECRET_PAIR.sub(lambda m: m.group(1) + '"' + REDACTED + '"', text)
    return _LONG_RUN.sub(lambda m: REDACTED if _looks_random(m.group(0)) else m.group(0), text)


def redact(value: Any, key: str = "") -> Any:
    """A deep copy of settings with secret-looking values replaced (key names stay visible)."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        if key and SECRET_KEY.search(key) and value:
            return REDACTED
        # All or nothing: a partly-masked value echoed back would be written as-is, whereas the bare
        # placeholder means "keep the stored value" (see restore_placeholders).
        return REDACTED if redact_text(value) != value else value
    return value


def is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY.search(key))


def restore_placeholders(new: Any, old: Any) -> Any:
    """Where the agent echoed «redacted» back, keep the existing (real) value."""
    if new == REDACTED:
        return old
    if isinstance(new, dict):
        old_dict = old if isinstance(old, dict) else {}
        return {k: restore_placeholders(v, old_dict.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(old, list) and len(new) == len(old):
        return [restore_placeholders(n, o) for n, o in zip(new, old, strict=True)]
    return new
