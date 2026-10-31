"""Bounded public summaries; never export arbitrary adapter responses or exception text."""

import math
import os
import re

SENSITIVE = re.compile(r"api.?key|password|secret|cookie|authorization|credential|token", re.I)
TEXT_SECRET = re.compile(
    r"api.?key|password|secret|cookie|authorization|credential|\btoken\b|bearer\s+|\bsk-[\w-]+",
    re.I,
)


def safe_text(value, limit=320):
    text = str(value)
    if TEXT_SECRET.search(text):
        return "[REDACTED]"
    for key, secret in os.environ.items():
        if SENSITIVE.search(key) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return text[:limit] + ("… [truncated]" if len(text) > limit else "")


def sanitize(value, limit=320, depth=0):
    if depth > 4:
        return "[truncated: depth limit]"
    if isinstance(value, dict):
        result = {
            safe_text(k, 80): (
                "[REDACTED]" if SENSITIVE.search(str(k)) else sanitize(v, limit, depth + 1)
            )
            for k, v in list(value.items())[:24]
        }
        if len(value) > 24 or any(isinstance(v, str) and len(v) > limit for v in value.values()):
            result["truncated"] = True
        return result
    if isinstance(value, (tuple, list)):
        return [sanitize(v, limit, depth + 1) for v in value[:24]]
    if isinstance(value, str):
        return safe_text(value, limit)
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return "[unsupported value omitted]"
