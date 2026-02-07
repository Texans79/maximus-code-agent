"""Configuration loader: .mca/config.yaml + env vars."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "workspace": ".",
    "approval_mode": "ask",          # auto | ask | paranoid
    "llm": {
        "base_url": "http://localhost:8000/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct-AWQ",
        "api_key": "not-needed",
        "temperature": 0.3,
        "max_tokens": 4096,
    },
    "shell": {
        "timeout": 120,
        "denylist": [
            "rm -rf /", "rm -rf /*", "mkfs", "dd if=", "shutdown", "reboot",
            "chmod -R 777", "chown -R", "curl|bash", "wget|bash",
            "curl | bash", "wget | bash", "> /dev/sda", ":(){ :|:& };:",
        ],
        "allowlist": [],
    },
    "git": {
        "auto_checkpoint": True,
        "branch_prefix": "mca/",
    },
    "memory": {
        "backend": "sqlite",          # sqlite | postgres
        "sqlite_path": ".mca/memory.db",
        "postgres_dsn": "",
    },
    "telegram": {
        "token": "",
        "allowed_users": [],
    },
    "telemetry": {
        "gpu_enabled": True,
        "nvme_enabled": False,
    },
    "style": {
        "indent": 4,
        "quotes": "double",
        "docstrings": "google",
        "max_line_length": 100,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = base.copy()
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


class Config:
    """Immutable-ish config object with attribute access."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            v = self._data[name]
        except KeyError:
            raise AttributeError(name)
        if isinstance(v, dict):
            return Config(v)
        return v

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"Config({self._data!r})"


def _env_overrides() -> dict[str, Any]:
    """Pull config from env vars: MCA_WORKSPACE, MCA_LLM_BASE_URL, etc."""
    overrides: dict[str, Any] = {}
    mapping = {
        "MCA_WORKSPACE": ("workspace",),
        "MCA_APPROVAL_MODE": ("approval_mode",),
        "MCA_LLM_BASE_URL": ("llm", "base_url"),
        "MCA_LLM_MODEL": ("llm", "model"),
        "MCA_LLM_API_KEY": ("llm", "api_key"),
        "MCA_TELEGRAM_TOKEN": ("telegram", "token"),
        "MCA_MEMORY_BACKEND": ("memory", "backend"),
        "MCA_MEMORY_POSTGRES_DSN": ("memory", "postgres_dsn"),
    }
    for env_key, path in mapping.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        d = overrides
        for part in path[:-1]:
            d = d.setdefault(part, {})
        d[path[-1]] = val
    return overrides


def load_config(project_dir: str | Path | None = None) -> Config:
    """Load config from .mca/config.yaml merged with defaults and env."""
    base = dict(_DEFAULTS)
    if project_dir:
        cfg_path = Path(project_dir) / ".mca" / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                file_cfg = yaml.safe_load(f) or {}
            base = _deep_merge(base, file_cfg)
    base = _deep_merge(base, _env_overrides())
    return Config(base)
