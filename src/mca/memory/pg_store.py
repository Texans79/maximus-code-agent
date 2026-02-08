"""PostgreSQL + pgvector memory store (PRIMARY backend).

Uses the 'mca' schema within the shared database. Runs migrations on first
connect to ensure all tables and indexes exist.
"""
from __future__ import annotations

import json
from typing import Any

from mca.log import get_logger
from mca.memory.base import MemoryStore

log = get_logger("memory.pg")


class PgMemoryStore(MemoryStore):
    """PostgreSQL + pgvector backed memory store.

    This is the PRIMARY backend. It provides:
    - Full-text search via tsvector/tsquery
    - Vector similarity search via pgvector HNSW index
    - Structured tables for tasks, steps, artifacts, tools, evaluations
    """

    def __init__(self, dsn: str) -> None:
        import psycopg  # raises ImportError if not installed

        self.conn = psycopg.connect(dsn, autocommit=True)
        self._run_migrations()
        log.info("PostgreSQL memory store connected")

    def _run_migrations(self) -> None:
        from mca.memory.migrations import run_migrations
        applied = run_migrations(self.conn)
        if applied:
            log.info("Applied %d migration(s)", applied)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def backend_name(self) -> str:
        return "postgres"

    @property
    def is_fallback(self) -> bool:
        return False

    # ── Knowledge (long-term memory) ─────────────────────────────────────

    def add(self, content: str, tags: list[str] | None = None,
            project: str = "", category: str = "general",
            metadata: dict | None = None,
            embedding: list[float] | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.knowledge (content, tags, project, category, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (
                content,
                tags or [],
                project,
                category,
                json.dumps(metadata or {}),
                embedding,
            ),
        ).fetchone()
        entry_id = row[0]
        log.info("stored knowledge %s (%d chars)", entry_id[:8], len(content))
        return entry_id

    def search(self, query: str, limit: int = 5,
               tags: list[str] | None = None, project: str = "") -> list[dict[str, Any]]:
        sql = """\
            SELECT id::text, content, tags, project, category, metadata, created,
                   ts_rank(to_tsvector('english', content), plainto_tsquery('english', %s)) AS rank
            FROM mca.knowledge
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        """
        params: list[Any] = [query, query]

        if tags:
            sql += " AND tags @> %s"
            params.append(tags)

        if project:
            sql += " AND project = %s"
            params.append(project)

        sql += " ORDER BY rank DESC LIMIT %s"
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()
        return [self._knowledge_row(r) for r in rows]

    def vector_search(self, embedding: list[float], limit: int = 5,
                      project: str = "") -> list[dict[str, Any]]:
        sql = """\
            SELECT id::text, content, tags, project, category, metadata, created,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM mca.knowledge
            WHERE embedding IS NOT NULL
        """
        params: list[Any] = [embedding]

        if project:
            sql += " AND project = %s"
            params.append(project)

        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([embedding, limit])

        rows = self.conn.execute(sql, params).fetchall()
        return [
            {**self._knowledge_row(r), "similarity": float(r[7])}
            for r in rows
        ]

    def get(self, entry_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id::text, content, tags, project, category, metadata, created "
            "FROM mca.knowledge WHERE id = %s::uuid",
            (entry_id,),
        ).fetchone()
        return self._knowledge_row(row) if row else None

    def delete(self, entry_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM mca.knowledge WHERE id = %s::uuid", (entry_id,)
        )
        return cur.rowcount > 0

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id::text, content, tags, project, category, metadata, created "
            "FROM mca.knowledge ORDER BY created DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [self._knowledge_row(r) for r in rows]

    @staticmethod
    def _knowledge_row(row) -> dict[str, Any]:
        return {
            "id": row[0],
            "content": row[1],
            "tags": list(row[2]) if row[2] else [],
            "project": row[3],
            "category": row[4],
            "metadata": row[5] if isinstance(row[5], dict) else json.loads(row[5] or "{}"),
            "created": str(row[6]),
        }

    # ── Tasks ────────────────────────────────────────────────────────────

    def create_task(self, description: str, workspace: str = "",
                    config: dict | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.tasks (description, workspace, config)
            VALUES (%s, %s, %s)
            RETURNING id::text
            """,
            (description, workspace, json.dumps(config or {})),
        ).fetchone()
        return row[0]

    def update_task(self, task_id: str, **fields) -> None:
        if not fields:
            return
        allowed = {"status", "result", "description", "workspace"}
        parts, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "result":
                v = json.dumps(v) if not isinstance(v, str) else v
            parts.append(f"{k} = %s")
            params.append(v)
        parts.append("updated = NOW()")
        params.append(task_id)
        self.conn.execute(
            f"UPDATE mca.tasks SET {', '.join(parts)} WHERE id = %s::uuid",
            params,
        )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id::text, description, status, workspace, config, result, created, updated "
            "FROM mca.tasks WHERE id = %s::uuid",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "description": row[1], "status": row[2],
            "workspace": row[3],
            "config": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
            "result": row[5] if isinstance(row[5], dict) else json.loads(row[5] or "null"),
            "created": str(row[6]), "updated": str(row[7]),
        }

    # ── Steps ────────────────────────────────────────────────────────────

    def add_step(self, task_id: str, action: str, agent_role: str = "orchestrator",
                 input_data: dict | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.steps (task_id, action, agent_role, input, seq)
            VALUES (%s::uuid, %s, %s, %s,
                    (SELECT COALESCE(MAX(seq), 0) + 1 FROM mca.steps WHERE task_id = %s::uuid))
            RETURNING id::text
            """,
            (task_id, action, agent_role,
             json.dumps(input_data) if input_data else None, task_id),
        ).fetchone()
        return row[0]

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
            parts.append(f"{k} = %s")
            params.append(v)
        if not parts:
            return
        params.append(step_id)
        self.conn.execute(
            f"UPDATE mca.steps SET {', '.join(parts)} WHERE id = %s::uuid",
            params,
        )

    # ── Artifacts ────────────────────────────────────────────────────────

    def add_artifact(self, task_id: str, path: str, action: str,
                     diff: str | None = None, step_id: str | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.artifacts (task_id, step_id, path, action, diff)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s)
            RETURNING id::text
            """,
            (task_id, step_id, path, action, diff),
        ).fetchone()
        return row[0]

    # ── Tools ────────────────────────────────────────────────────────────

    def log_tool(self, task_id: str | None, tool_name: str, command: str = "",
                 exit_code: int = 0, stdout: str = "", stderr: str = "",
                 duration_ms: int = 0, step_id: str | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.tools (task_id, step_id, tool_name, command, exit_code, stdout, stderr, duration_ms)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (task_id, step_id, tool_name, command, exit_code, stdout, stderr, duration_ms),
        ).fetchone()
        return row[0]

    # ── Evaluations ──────────────────────────────────────────────────────

    def add_evaluation(self, task_id: str, verdict: str, evaluator: str = "reviewer",
                       issues: list | None = None, comments: str = "",
                       step_id: str | None = None) -> str:
        row = self.conn.execute(
            """\
            INSERT INTO mca.evaluations (task_id, step_id, evaluator, verdict, issues, comments)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
            RETURNING id::text
            """,
            (task_id, step_id, evaluator, verdict, json.dumps(issues or []), comments),
        ).fetchone()
        return row[0]

    # ── Lifecycle ────────────────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()
