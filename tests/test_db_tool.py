"""Tests for the database tool (read-only SQL)."""
import pytest
from unittest.mock import MagicMock, PropertyMock

from mca.tools.db_tool import DbTool, _BLOCKED_PATTERN


class TestSqlValidation:
    def _tool(self):
        return DbTool(conn=MagicMock())

    def test_select_allowed(self):
        assert self._tool()._validate_sql("SELECT * FROM mca.tasks") is None

    def test_with_cte_allowed(self):
        assert self._tool()._validate_sql("WITH x AS (SELECT 1) SELECT * FROM x") is None

    def test_explain_allowed(self):
        assert self._tool()._validate_sql("EXPLAIN SELECT 1") is None

    def test_show_allowed(self):
        assert self._tool()._validate_sql("SHOW search_path") is None

    def test_insert_blocked(self):
        err = self._tool()._validate_sql("INSERT INTO mca.tasks VALUES (1)")
        assert err is not None
        assert "not allowed" in err.lower() or "only select" in err.lower()

    def test_update_blocked(self):
        err = self._tool()._validate_sql("UPDATE mca.tasks SET status='done'")
        assert err is not None

    def test_delete_blocked(self):
        err = self._tool()._validate_sql("DELETE FROM mca.tasks")
        assert err is not None

    def test_drop_blocked(self):
        err = self._tool()._validate_sql("DROP TABLE mca.tasks")
        assert err is not None

    def test_alter_blocked(self):
        err = self._tool()._validate_sql("ALTER TABLE mca.tasks ADD COLUMN x int")
        assert err is not None

    def test_truncate_blocked(self):
        err = self._tool()._validate_sql("TRUNCATE mca.tasks")
        assert err is not None

    def test_create_blocked(self):
        err = self._tool()._validate_sql("CREATE TABLE evil (id int)")
        assert err is not None

    def test_select_with_delete_subquery_blocked(self):
        err = self._tool()._validate_sql("SELECT * FROM (DELETE FROM mca.tasks RETURNING *)")
        assert err is not None

    def test_case_insensitive(self):
        err = self._tool()._validate_sql("DrOp TABLE mca.tasks")
        assert err is not None

    def test_non_select_prefix_blocked(self):
        err = self._tool()._validate_sql("CALL some_procedure()")
        assert err is not None
        assert "Only SELECT" in err


class TestQueryExecution:
    def _mock_conn(self, columns, rows):
        conn = MagicMock()
        cur = MagicMock()
        desc = [(col,) for col in columns]
        type(cur).description = PropertyMock(return_value=desc)
        cur.fetchmany.return_value = rows
        conn.execute.return_value = cur
        return conn

    def test_simple_select(self):
        conn = self._mock_conn(["id", "name"], [(1, "alice"), (2, "bob")])
        tool = DbTool(conn)
        result = tool.execute("query_db", {"sql": "SELECT id, name FROM mca.tasks"})
        assert result.ok
        assert result.data["row_count"] == 2
        assert result.data["columns"] == ["id", "name"]
        assert result.data["rows"][0] == {"id": 1, "name": "alice"}

    def test_empty_sql_rejected(self):
        tool = DbTool(MagicMock())
        result = tool.execute("query_db", {"sql": ""})
        assert not result.ok

    def test_blocked_sql_not_executed(self):
        conn = MagicMock()
        tool = DbTool(conn)
        result = tool.execute("query_db", {"sql": "DROP TABLE mca.tasks"})
        assert not result.ok
        conn.execute.assert_not_called()

    def test_limit_capped_at_200(self):
        conn = self._mock_conn(["x"], [])
        tool = DbTool(conn)
        tool.execute("query_db", {"sql": "SELECT 1", "limit": 500})
        conn.execute.assert_called_once()
        # fetchmany should be called with 200 (capped)
        cur = conn.execute.return_value
        cur.fetchmany.assert_called_with(200)

    def test_query_exception_returns_error(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("connection lost")
        tool = DbTool(conn)
        result = tool.execute("query_db", {"sql": "SELECT 1"})
        assert not result.ok
        assert "connection lost" in result.error


class TestListTables:
    def test_returns_table_list(self):
        conn = MagicMock()
        # First call: information_schema query
        info_cur = MagicMock()
        info_cur.fetchall.return_value = [("tasks",), ("journal",)]
        # Count queries
        count_cur = MagicMock()
        count_cur.fetchone.return_value = (42,)
        conn.execute.side_effect = [info_cur, count_cur, count_cur]
        tool = DbTool(conn)
        result = tool.execute("list_tables", {})
        assert result.ok
        assert len(result.data["tables"]) == 2


class TestDescribeTable:
    def test_describe_valid_table(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = [
            ("id", "uuid", "NO", None),
            ("status", "text", "YES", "'pending'"),
        ]
        conn.execute.return_value = cur
        tool = DbTool(conn)
        result = tool.execute("describe_table", {"table": "tasks"})
        assert result.ok
        assert len(result.data["columns"]) == 2
        assert result.data["columns"][0]["name"] == "id"

    def test_empty_table_name(self):
        tool = DbTool(MagicMock())
        result = tool.execute("describe_table", {"table": ""})
        assert not result.ok

    def test_sql_injection_blocked(self):
        tool = DbTool(MagicMock())
        result = tool.execute("describe_table", {"table": "tasks; DROP TABLE mca.tasks"})
        assert not result.ok
        assert "Invalid table name" in result.error


class TestVerify:
    def test_verify_connected(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (1,)
        conn.execute.return_value = cur
        tool = DbTool(conn)
        result = tool.verify()
        assert result.ok

    def test_verify_disconnected(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("connection refused")
        tool = DbTool(conn)
        result = tool.verify()
        assert not result.ok


class TestToolDefinitions:
    def test_has_three_actions(self):
        tool = DbTool(MagicMock())
        defs = tool.tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "query_db" in names
        assert "list_tables" in names
        assert "describe_table" in names
        assert len(defs) == 3

    def test_query_db_has_sql_param(self):
        tool = DbTool(MagicMock())
        defs = tool.tool_definitions()
        query_def = [d for d in defs if d["function"]["name"] == "query_db"][0]
        assert "sql" in query_def["function"]["parameters"]["properties"]
        assert "sql" in query_def["function"]["parameters"]["required"]
