"""Memory store abstraction layer.

PostgreSQL is the PRIMARY and DEFAULT backend.
SQLite exists ONLY as an emergency fallback when PostgreSQL is unreachable.
A clear warning is emitted when operating in fallback mode.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from mca.config import Config
from mca.log import console, get_logger

log = get_logger("memory")


class MemoryStore(ABC):
    """Abstract interface for memory storage.

    All backends must implement this interface so the storage layer can be
    swapped without changing calling code.
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Return 'postgres' or 'sqlite'."""

    @property
    @abstractmethod
    def is_fallback(self) -> bool:
        """True if running in degraded/fallback mode."""

    # ── Knowledge (long-term memory) ─────────────────────────────────────

    @abstractmethod
    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", category: str = "general",
            metadata: dict | None = None,
            embedding: list[float] | None = None) -> str:
        """Store a knowledge entry. Returns the entry ID."""

    @abstractmethod
    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        """Full-text search on knowledge entries."""

    @abstractmethod
    def vector_search(self, embedding: list[float], limit: int = 5,
                      project: str = "") -> list[dict[str, Any]]:
        """Similarity search using pgvector embeddings."""

    @abstractmethod
    def get(self, entry_id: str) -> dict[str, Any] | None:
        """Get a single knowledge entry by ID."""

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """Delete a knowledge entry."""

    @abstractmethod
    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """List most recent knowledge entries."""

    # ── Tasks ────────────────────────────────────────────────────────────

    @abstractmethod
    def create_task(self, description: str, workspace: str = "",
                    config: dict | None = None) -> str:
        """Create a task record. Returns task ID."""

    @abstractmethod
    def update_task(self, task_id: str, **fields) -> None:
        """Update task fields (status, result, etc.)."""

    @abstractmethod
    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID."""

    # ── Steps ────────────────────────────────────────────────────────────

    @abstractmethod
    def add_step(self, task_id: str, action: str, agent_role: str = "orchestrator",
                 input_data: dict | None = None) -> str:
        """Add a step to a task. Returns step ID."""

    @abstractmethod
    def update_step(self, step_id: str, **fields) -> None:
        """Update step fields (status, output, duration_ms)."""

    # ── Artifacts ────────────────────────────────────────────────────────

    @abstractmethod
    def add_artifact(self, task_id: str, path: str, action: str,
                     diff: str | None = None, step_id: str | None = None) -> str:
        """Record a file artifact. Returns artifact ID."""

    # ── Tools ────────────────────────────────────────────────────────────

    @abstractmethod
    def log_tool(self, task_id: str | None, tool_name: str, command: str = "",
                 exit_code: int = 0, stdout: str = "", stderr: str = "",
                 duration_ms: int = 0, step_id: str | None = None) -> str:
        """Log a tool execution. Returns log entry ID."""

    # ── Evaluations ──────────────────────────────────────────────────────

    @abstractmethod
    def add_evaluation(self, task_id: str, verdict: str, evaluator: str = "reviewer",
                       issues: list | None = None, comments: str = "",
                       step_id: str | None = None) -> str:
        """Record an evaluation. Returns evaluation ID."""


def _resolve_dsn(config: Config) -> str:
    """Resolve PostgreSQL DSN from env vars or config, in priority order."""
    # 1. DATABASE_URL (most common convention)
    dsn = os.environ.get("DATABASE_URL")
    if dsn and "postgresql" in dsn:
        return dsn

    # 2. MCA-specific env var
    dsn = os.environ.get("MCA_MEMORY_POSTGRES_DSN")
    if dsn:
        return dsn

    # 3. Build from PG* env vars
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    user = os.environ.get("PGUSER", "maximus_user")
    password = os.environ.get("PGPASSWORD", "")
    dbname = os.environ.get("PGDATABASE", "openwebui")
    if password:
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?options=-csearch_path%3Dmca,public"

    # 4. Config file
    cfg_dsn = config.memory.get("postgres_dsn", "")
    if cfg_dsn:
        return cfg_dsn

    # 5. Default for this machine
    return "postgresql://maximus_user@localhost:5432/openwebui?options=-csearch_path%3Dmca,public"


def get_store(config: Config) -> MemoryStore:
    """Factory: connect to PostgreSQL (primary) or fall back to SQLite (emergency).

    PostgreSQL is ALWAYS attempted first. SQLite is ONLY used if PostgreSQL
    is unreachable, and a loud warning is emitted.
    """
    dsn = _resolve_dsn(config)

    # ── Try PostgreSQL first (PRIMARY) ───────────────────────────────────
    try:
        from mca.memory.pg_store import PgMemoryStore
        store = PgMemoryStore(dsn)
        log.info("Connected to PostgreSQL: %s", dsn.split("@")[-1] if "@" in dsn else dsn[:40])
        return store
    except ImportError:
        log.error(
            "psycopg not installed — cannot connect to PostgreSQL. "
            "Install with: pip install 'maximus-code-agent[pg]'"
        )
        console.print(
            "[error]MEMORY DEGRADED: psycopg not installed. "
            "Install with: pip install 'maximus-code-agent[pg]'[/error]"
        )
    except Exception as e:
        log.error("PostgreSQL connection failed: %s", e)
        console.print(
            f"[error]MEMORY DEGRADED: PostgreSQL unreachable — {e}\n"
            f"  DSN: {dsn.split('@')[-1] if '@' in dsn else dsn[:40]}\n"
            f"  Falling back to SQLite (emergency mode). "
            f"Data will NOT persist across sessions properly.[/error]"
        )

    # ── SQLite fallback (EMERGENCY ONLY) ─────────────────────────────────
    console.print(
        "[warn]WARNING: Operating in SQLite fallback mode. "
        "Long-term memory, embeddings, and cross-session recall are degraded. "
        "Fix PostgreSQL connection to restore full functionality.[/warn]"
    )
    from mca.memory.sqlite_store import SqliteMemoryStore
    return SqliteMemoryStore(config.memory.get("sqlite_path", ".mca/memory.db"))
