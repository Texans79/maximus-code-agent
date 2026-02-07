"""Memory store interface and factory."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from mca.config import Config


class MemoryStore(ABC):
    """Abstract interface for memory storage."""

    @abstractmethod
    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", metadata: dict | None = None) -> str:
        """Store a memory entry. Returns the entry ID."""

    @abstractmethod
    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        """Search memories by text. Returns list of matching entries."""

    @abstractmethod
    def get(self, entry_id: str) -> dict[str, Any] | None:
        """Get a single entry by ID."""

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """Delete an entry. Returns True if deleted."""

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """List most recent entries."""


def get_store(config: Config) -> MemoryStore:
    """Factory: return the configured memory store."""
    backend = config.memory.backend

    if backend == "postgres":
        try:
            from mca.memory.pg_store import PgMemoryStore
            return PgMemoryStore(config.memory.postgres_dsn)
        except ImportError:
            from mca.log import get_logger
            get_logger("memory").warning(
                "Postgres backend requested but psycopg not installed. Falling back to SQLite."
            )

    from mca.memory.sqlite_store import SqliteMemoryStore
    return SqliteMemoryStore(config.memory.sqlite_path)
