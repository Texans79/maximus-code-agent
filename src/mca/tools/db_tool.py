"""DbTool — read-only SQL access to PostgreSQL for MCA.

Allows querying any table in the mca schema (and public). Blocks all
write operations (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE).
"""
from __future__ import annotations

import re
from typing import Any

from mca.tools.base import ToolBase, ToolResult, _param

# SQL statements that are allowed (read-only)
_ALLOWED_PREFIXES = ("select", "with", "explain", "show", "\\d")

# SQL keywords that indicate a write/destructive operation
_BLOCKED_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|vacuum)\b",
    re.IGNORECASE,
)


class DbTool(ToolBase):
    """Read-only SQL query tool for PostgreSQL."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @property
    def name(self) -> str:
        return "database"

    @property
    def description(self) -> str:
        return "Run read-only SQL queries against PostgreSQL (mca schema)"

    def actions(self) -> dict[str, str]:
        return {
            "query_db": "Execute a read-only SQL query and return results",
            "list_tables": "List all tables in the mca schema",
            "describe_table": "Show columns and types for a table",
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "query_db",
                "description": (
                    "Execute a read-only SQL query against PostgreSQL. "
                    "Only SELECT/WITH/EXPLAIN allowed. Returns up to 50 rows. "
                    "Tables are in the 'mca' schema: tasks, steps, artifacts, "
                    "knowledge, tools, evaluations, run_metrics, journal, "
                    "graph_nodes, graph_edges, migrations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": _param("string", "SQL query (SELECT only)"),
                        "limit": _param("integer", "Max rows to return (default: 50, max: 200)"),
                    },
                    "required": ["sql"],
                },
            }},
            {"type": "function", "function": {
                "name": "list_tables",
                "description": "List all tables in the mca schema with row counts",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            }},
            {"type": "function", "function": {
                "name": "describe_table",
                "description": "Show column names, types, and nullable for a table",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "table": _param("string", "Table name (e.g. 'run_metrics', 'journal')"),
                    },
                    "required": ["table"],
                },
            }},
        ]

    def _validate_sql(self, sql: str) -> str | None:
        """Return error message if SQL is not safe, or None if OK."""
        stripped = sql.strip().lower()
        if not any(stripped.startswith(p) for p in _ALLOWED_PREFIXES):
            return f"Only SELECT/WITH/EXPLAIN queries allowed. Got: {stripped[:30]}..."
        if _BLOCKED_PATTERN.search(sql):
            match = _BLOCKED_PATTERN.search(sql)
            return f"Blocked: write operation '{match.group()}' not allowed"
        return None

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "query_db":
            return self._query(args)
        if action == "list_tables":
            return self._list_tables()
        if action == "describe_table":
            return self._describe_table(args)
        raise ValueError(f"Unknown database action: {action}")

    def _query(self, args: dict[str, Any]) -> ToolResult:
        sql = args.get("sql", "").strip()
        if not sql:
            return ToolResult(ok=False, error="No SQL query provided")

        err = self._validate_sql(sql)
        if err:
            return ToolResult(ok=False, error=err)

        limit = min(args.get("limit", 50), 200)

        try:
            cur = self._conn.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows_raw = cur.fetchmany(limit)
            rows = [dict(zip(columns, row)) for row in rows_raw]

            return ToolResult(ok=True, data={
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": len(rows_raw) == limit,
            })
        except Exception as e:
            return ToolResult(ok=False, error=f"Query failed: {e}")

    def _list_tables(self) -> ToolResult:
        try:
            cur = self._conn.execute("""
                SELECT table_name,
                       (SELECT count(*) FROM mca.migrations) as example
                FROM information_schema.tables
                WHERE table_schema = 'mca'
                ORDER BY table_name
            """)
            tables = [row[0] for row in cur.fetchall()]

            # Get actual row counts
            result = []
            for t in tables:
                try:
                    cnt_cur = self._conn.execute(f"SELECT count(*) FROM mca.{t}")
                    count = cnt_cur.fetchone()[0]
                    result.append({"table": t, "rows": count})
                except Exception:
                    result.append({"table": t, "rows": "?"})

            return ToolResult(ok=True, data={"tables": result})
        except Exception as e:
            return ToolResult(ok=False, error=f"Failed to list tables: {e}")

    def _describe_table(self, args: dict[str, Any]) -> ToolResult:
        table = args.get("table", "").strip()
        if not table:
            return ToolResult(ok=False, error="No table name provided")

        # Sanitize table name — alphanumeric and underscore only
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table):
            return ToolResult(ok=False, error=f"Invalid table name: {table}")

        try:
            cur = self._conn.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'mca' AND table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            columns = []
            for row in cur.fetchall():
                columns.append({
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2],
                    "default": row[3],
                })

            if not columns:
                return ToolResult(ok=False, error=f"Table 'mca.{table}' not found")

            return ToolResult(ok=True, data={"table": table, "columns": columns})
        except Exception as e:
            return ToolResult(ok=False, error=f"Failed to describe table: {e}")

    def verify(self) -> ToolResult:
        try:
            cur = self._conn.execute("SELECT 1")
            cur.fetchone()
            return ToolResult(ok=True, data={"tool": "database", "status": "connected"})
        except Exception as e:
            return ToolResult(ok=False, error=f"DB not reachable: {e}")
