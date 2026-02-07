"""Postgres + pgvector memory store (optional dependency)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from mca.log import get_logger
from mca.memory.base import MemoryStore

log = get_logger("memory.pg")

_SCHEMA = """\
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tags JSONB DEFAULT '[]',
    project TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    embedding vector(384)
);

CREATE INDEX IF NOT EXISTS memories_content_idx ON memories USING gin (to_tsvector('english', content));
CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""


class PgMemoryStore(MemoryStore):
    """Postgres + pgvector backed memory store with text search and embeddings."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError:
            raise ImportError("Install psycopg: pip install 'maximus-code-agent[pg]'")

        self.conn = psycopg.connect(dsn, autocommit=True)
        try:
            self.conn.execute(_SCHEMA)
        except Exception as e:
            log.warning("Schema creation (may already exist): %s", e)
        log.info("Postgres memory store connected")

    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", metadata: dict | None = None) -> str:
        entry_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc)
        self.conn.execute(
            "INSERT INTO memories (id, content, tags, project, metadata, created) VALUES (%s, %s, %s, %s, %s, %s)",
            (entry_id, content, json.dumps(tags or []), project, json.dumps(metadata or {}), now),
        )
        log.info("stored memory %s (%d chars) [postgres]", entry_id, len(content))
        return entry_id

    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        sql = """\
            SELECT id, content, tags, project, metadata, created,
                   ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) as rank
            FROM memories
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        """
        params: list[Any] = [query, query]

        if project:
            sql += " AND project = %s"
            params.append(project)

        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0], "content": r[1],
                "tags": r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]"),
                "project": r[3], "metadata": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                "created": str(r[5]),
            }
            for r in rows
        ]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM memories WHERE id = %s", (entry_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "content": row[1], "tags": row[2], "project": row[3],
                "metadata": row[4], "created": str(row[5])}

    def delete(self, entry_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = %s", (entry_id,))
        return cur.rowcount > 0

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, content, tags, project, metadata, created FROM memories ORDER BY created DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            {"id": r[0], "content": r[1], "tags": r[2], "project": r[3],
             "metadata": r[4], "created": str(r[5])}
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()
