"""Structured JSON logging + rich console output."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.theme import Theme

_theme = Theme({
    "info": "cyan",
    "warn": "yellow bold",
    "error": "red bold",
    "success": "green bold",
    "dim": "dim",
})
console = Console(theme=_theme, stderr=True)

_json_handler: logging.FileHandler | None = None


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["error"] = str(record.exc_info[1])
        if hasattr(record, "extra_data"):
            entry["data"] = record.extra_data
        return json.dumps(entry)


def setup_logging(log_dir: str | Path | None = None, verbose: bool = False) -> None:
    """Configure root logger with JSON file + stderr console."""
    root = logging.getLogger("mca")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    # Console handler (human-readable)
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    root.addHandler(ch)

    # JSON file handler
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        global _json_handler
        _json_handler = logging.FileHandler(log_path / "mca.jsonl")
        _json_handler.setFormatter(_JsonFormatter())
        root.addHandler(_json_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mca.{name}")
