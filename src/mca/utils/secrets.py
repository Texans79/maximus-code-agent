"""Redact secrets from text before logging or display."""
from __future__ import annotations

import os
import re

# Patterns that look like secrets
_SECRET_PATTERNS = [
    re.compile(r'(?i)(token|key|secret|password|passwd|auth|credential)[\s=:]+\S+'),
    re.compile(r'(?:ghp_|sk-|xoxb-|xoxp-|AKIA)[A-Za-z0-9_\-]+'),
    re.compile(r'eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+'),  # JWT
]

# Env var names that are secret
_SECRET_ENV_KEYS = {
    "MCA_LLM_API_KEY", "MCA_TELEGRAM_TOKEN", "MCA_MEMORY_POSTGRES_DSN",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL",
    "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN",
}


def redact(text: str) -> str:
    """Replace probable secrets with [REDACTED]."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    # Redact known env var values that appear in text
    for key in _SECRET_ENV_KEYS:
        val = os.environ.get(key)
        if val and len(val) > 4 and val in text:
            text = text.replace(val, "[REDACTED]")
    return text


def safe_env_dump() -> dict[str, str]:
    """Return env dict with secret values masked."""
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if k.upper() in _SECRET_ENV_KEYS or any(
            w in k.upper() for w in ("TOKEN", "SECRET", "KEY", "PASS", "AUTH")
        ):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out
