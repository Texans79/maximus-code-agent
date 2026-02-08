"""SQLite-backed memory store (EMERGENCY FALLBACK ONLY).

This backend is used ONLY when PostgreSQL is unreachable. It provides basic
functionality but lacks vector similarity search and has limited cross-session
durability. A loud warning is emitted when this store is active.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mca.log import get_logger
from mca.memory.base import MemoryStore

log = get_logger("memory.sqlite")

_SCHEMA = """\
-- Knowledge table (long-term memory)
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    project TEXT DEFAULT '',
    category TEXT DEFAULT 'general',
    metadata TEXT DEFAULT '{}',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
    content, tags, project,
    content='knowledge',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS knowledge_ai AFTER INSERT ON knowledge BEGIN
    INSERT INTO knowledge_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_ad AFTER DELETE ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
END;
CREATE TRIGGER IF NOT EXISTS knowledge_au AFTER UPDATE ON knowledge BEGIN
    INSERT INTO knowledge_fts(knowledge_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
    INSERT INTO knowledge_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    workspace TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    result TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

-- Steps table
CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    seq INTEGER NOT NULL DEFAULT 0,
    agent_role TEXT NOT NULL DEFAULT 'orchestrator',
    action TEXT NOT NULL,
    input TEXT,
    output TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    duration_ms INTEGER,
    created TEXT NOT NULL
);

-- Artifacts table
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    step_id TEXT REFERENCES steps(id),
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    diff TEXT,
    created TEXT NOT NULL
);

-- Tools log table
CREATE TABLE IF NOT EXISTS tools (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    step_id TEXT REFERENCES steps(id),
    tool_name TEXT NOT NULL,
    command TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    duration_ms INTEGER,
    created TEXT NOT NULL
);

-- Evaluations table
CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES tasks(id),
    step_id TEXT REFERENCES steps(id),
    evaluator TEXT NOT NULL DEFAULT 'reviewer',
    verdict TEXT NOT NULL,
    issues TEXT DEFAULT '[]',
    comments TEXT,
    created TEXT NOT NULL
);
"""


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteMemoryStore(MemoryStore):
    """SQLite + FTS5 memory store (EMERGENCY FALLBACK).

    Lacks: pgvector similarity search, robust cross-session recall.
    """

    def __init__(self, db_path: str | Path = ".mca/memory.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        log.debug("SQLite fallback store: %s", self.db_path)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        return "sqlite"

    @property
    def is_fallback(self) -> bool:
        return True

    # ── Knowledge (long-term memory) ─────────────────────────────────────

    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", category: str = "general",
            metadata: dict | None = None,
            embedding: list[float] | None = None) -> str:
        entry_id = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO knowledge (id, content, tags, project, category, metadata, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, content, json.dumps(tags or []), project, category,
             json.dumps(metadata or {}), now, now),
        )
        self.conn.commit()
        log.info("stored knowledge %s (%d chars) [sqlite-fallback]", entry_id[:8], len(content))
        return entry_id

    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        sql = """\
            SELECT k.id, k.content, k.tags, k.project, k.category, k.metadata, k.created, rank
            FROM knowledge_fts fts
            JOIN knowledge k ON k.rowid = fts.rowid
            WHERE knowledge_fts MATCH ?
        """
        params: list[Any] = [query]

        if project:
            sql += " AND k.project = ?"
            params.append(project)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [self._knowledge_row(r) for r in rows]

    def vector_search(self, embedding: list[float], limit: int = 5,
                      project: str = "") -> list[dict[str, Any]]:
        log.warning("vector_search not available in SQLite fallback mode")
        return []

    def get(self, entry_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, content, tags, project, category, metadata, created "
            "FROM knowledge WHERE id = ?",
            (entry_id,),
        ).fetchone()
        return self._knowledge_row(row) if row else None

    def delete(self, entry_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM knowledge WHERE id = ?", (entry_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, content, tags, project, category, metadata, created "
            "FROM knowledge ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._knowledge_row(r) for r in rows]

    @staticmethod
    def _knowledge_row(row) -> dict[str, Any]:
        return {
            "id": row[0],
            "content": row[1],
            "tags": json.loads(row[2]) if isinstance(row[2], str) else (row[2] or []),
            "project": row[3],
            "category": row[4],
            "metadata": json.loads(row[5]) if isinstance(row[5], str) else (row[5] or {}),
            "created": row[6],
        }

    # ── Tasks ────────────────────────────────────────────────────────────

    def create_task(self, description: str, workspace: str = "",
                    config: dict | None = None) -> str:
        tid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO tasks (id, description, workspace, config, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tid, description, workspace, json.dumps(config or {}), now, now),
        )
        self.conn.commit()
        return tid

    def update_task(self, task_id: str, **fields) -> None:
        if not fields:
            return
        allowed = {"status", "result", "description", "workspace"}
        parts, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "result" and not isinstance(v, str):
                v = json.dumps(v)
            parts.append(f"{k} = ?")
            params.append(v)
        parts.append("updated = ?")
        params.append(_now())
        params.append(task_id)
        self.conn.execute(f"UPDATE tasks SET {', '.join(parts)} WHERE id = ?", params)
        self.conn.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, description, status, workspace, config, result, created, updated "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "description": row[1], "status": row[2],
            "workspace": row[3],
            "config": json.loads(row[4] or "{}"),
            "result": json.loads(row[5]) if row[5] else None,
            "created": row[6], "updated": row[7],
        }

    # ── Steps ────────────────────────────────────────────────────────────

    def add_step(self, task_id: str, action: str, agent_role: str = "orchestrator",
                 input_data: dict | None = None) -> str:
        sid = _uid()
        now = _now()
        seq_row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM steps WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        seq = seq_row[0] if seq_row else 1
        self.conn.execute(
            "INSERT INTO steps (id, task_id, seq, agent_role, action, input, status, created) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (sid, task_id, seq, agent_role, action,
             json.dumps(input_data) if input_data else None, now),
        )
        self.conn.commit()
        return sid

    def update_step(self, step_id: str, **fields) -> None:
        if not fields:
            return
        allowed = {"status", "output", "duration_ms"}
        parts, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "output" and not isinstance(v, str):
                v = json.dumps(v)
            parts.append(f"{k} = ?")
            params.append(v)
        if not parts:
            return
        params.append(step_id)
        self.conn.execute(f"UPDATE steps SET {', '.join(parts)} WHERE id = ?", params)
        self.conn.commit()

    # ── Artifacts ────────────────────────────────────────────────────────

    def add_artifact(self, task_id: str, path: str, action: str,
                     diff: str | None = None, step_id: str | None = None) -> str:
        aid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO artifacts (id, task_id, step_id, path, action, diff, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, task_id, step_id, path, action, diff, now),
        )
        self.conn.commit()
        return aid

    # ── Tools ────────────────────────────────────────────────────────────

    def log_tool(self, task_id: str | None, tool_name: str, command: str = "",
                 exit_code: int = 0, stdout: str = "", stderr: str = "",
                 duration_ms: int = 0, step_id: str | None = None) -> str:
        lid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO tools (id, task_id, step_id, tool_name, command, exit_code, stdout, stderr, duration_ms, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, task_id, step_id, tool_name, command, exit_code, stdout, stderr, duration_ms, now),
        )
        self.conn.commit()
        return lid

    # ── Evaluations ──────────────────────────────────────────────────────

    def add_evaluation(self, task_id: str, verdict: str, evaluator: str = "reviewer",
                       issues: list | None = None, comments: str = "",
                       step_id: str | None = None) -> str:
        eid = _uid()
        now = _now()
        self.conn.execute(
            "INSERT INTO evaluations (id, task_id, step_id, evaluator, verdict, issues, comments, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, task_id, step_id, evaluator, verdict, json.dumps(issues or []), comments, now),
        )
        self.conn.commit()
        return eid

    # ── Lifecycle ────────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()
