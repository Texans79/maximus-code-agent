"""SQLite-backed memory store with FTS5 full-text search."""
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
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    project TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    created TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, tags, project,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, tags, project)
    VALUES ('delete', old.rowid, old.content, old.tags, old.project);
    INSERT INTO memories_fts(rowid, content, tags, project)
    VALUES (new.rowid, new.content, new.tags, new.project);
END;
"""


class SqliteMemoryStore(MemoryStore):
    """SQLite + FTS5 memory store."""

    def __init__(self, db_path: str | Path = ".mca/memory.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        log.debug("SQLite memory store: %s", self.db_path)

    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", metadata: dict | None = None) -> str:
        entry_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO memories (id, content, tags, project, metadata, created) VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, content, json.dumps(tags or []), project, json.dumps(metadata or {}), now),
        )
        self.conn.commit()
        log.info("stored memory %s (%d chars)", entry_id, len(content))
        return entry_id

    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        # FTS5 search
        sql = """\
            SELECT m.id, m.content, m.tags, m.project, m.metadata, m.created,
                   rank
            FROM memories_fts fts
            JOIN memories m ON m.rowid = fts.rowid
            WHERE memories_fts MATCH ?
        """
        params: list[Any] = [query]

        if project:
            sql += " AND m.project = ?"
            params.append(project)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (entry_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, entry_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memories ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        return d

    def close(self) -> None:
        self.conn.close()
