"""Tests for run metrics — write, query last/summary/failures."""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from mca.memory.metrics import write_metrics, get_last, get_summary, get_failures, _row_to_dict


# ── Unit Tests (mocked conn) ─────────────────────────────────────────────────

class TestRowToDict:
    def test_converts_row_tuple(self):
        row = (
            "abc-123", "task-456",
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, 0, 5, tzinfo=timezone.utc),
            True, 7, 12, 3, 2, 1, False, None, "qwen-72b", 5000, 3000,
        )
        d = _row_to_dict(row)
        assert d["id"] == "abc-123"
        assert d["task_id"] == "task-456"
        assert d["success"] is True
        assert d["iterations"] == 7
        assert d["tool_calls"] == 12
        assert d["files_changed"] == 3
        assert d["tests_runs"] == 2
        assert d["lint_runs"] == 1
        assert d["rollback_used"] is False
        assert d["failure_reason"] is None
        assert d["model"] == "qwen-72b"
        assert d["token_prompt"] == 5000
        assert d["token_completion"] == 3000

    def test_failure_row(self):
        row = (
            "abc-999", None,
            datetime(2025, 1, 2, tzinfo=timezone.utc),
            datetime(2025, 1, 2, 0, 10, tzinfo=timezone.utc),
            False, 15, 30, 0, 3, 0, True,
            "Max iterations reached", "qwen-72b", 8000, 6000,
        )
        d = _row_to_dict(row)
        assert d["success"] is False
        assert d["rollback_used"] is True
        assert d["failure_reason"] == "Max iterations reached"
        assert d["task_id"] is None


class TestWriteMetrics:
    def test_calls_execute_with_correct_params(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = ("metric-id-1",)
        now = datetime.now(timezone.utc)
        mid = write_metrics(
            conn,
            task_id="task-abc",
            started_at=now - timedelta(seconds=60),
            ended_at=now,
            success=True,
            iterations=5,
            tool_calls=10,
            files_changed=2,
            tests_runs=1,
            lint_runs=0,
            rollback_used=False,
            failure_reason=None,
            model="qwen-72b",
            token_prompt=4000,
            token_completion=2000,
        )
        assert mid == "metric-id-1"
        conn.execute.assert_called_once()
        args = conn.execute.call_args
        sql = args[0][0]
        assert "INSERT INTO mca.run_metrics" in sql
        params = args[0][1]
        assert params[0] == "task-abc"  # task_id
        assert params[3] is True        # success
        assert params[4] == 5            # iterations
        assert params[5] == 10           # tool_calls

    def test_write_with_none_task_id(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = ("metric-id-2",)
        now = datetime.now(timezone.utc)
        mid = write_metrics(
            conn,
            task_id=None,
            started_at=now,
            ended_at=now,
            success=False,
            iterations=0,
            tool_calls=0,
            files_changed=0,
            tests_runs=0,
            lint_runs=0,
            rollback_used=False,
            failure_reason="Plan rejected by user",
            model=None,
            token_prompt=0,
            token_completion=0,
        )
        assert mid == "metric-id-2"


class TestGetLast:
    def test_returns_formatted_rows(self):
        conn = MagicMock()
        now = datetime.now(timezone.utc)
        conn.execute.return_value.fetchall.return_value = [
            ("id-1", "task-1", now, now, True, 5, 10, 2, 1, 0, False, None, "qwen", 3000, 1500),
        ]
        results = get_last(conn, limit=1)
        assert len(results) == 1
        assert results[0]["id"] == "id-1"
        assert results[0]["success"] is True

    def test_returns_empty_list(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        results = get_last(conn, limit=5)
        assert results == []


class TestGetSummary:
    def test_returns_aggregated_stats(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (
            10, 8, 2, 80.0, 6.5, 15.2, 50000, 30000, 45.3, 12, 3, 1,
        )
        s = get_summary(conn, days=7)
        assert s["total_runs"] == 10
        assert s["successes"] == 8
        assert s["failures"] == 2
        assert s["success_rate"] == 80.0
        assert s["avg_iterations"] == 6.5
        assert s["avg_tool_calls"] == 15.2
        assert s["total_prompt_tokens"] == 50000
        assert s["total_completion_tokens"] == 30000
        assert s["avg_duration_s"] == 45.3
        assert s["rollback_count"] == 1

    def test_zero_runs(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (
            0, 0, 0, 0.0, None, None, 0, 0, None, 0, 0, 0,
        )
        s = get_summary(conn, days=30)
        assert s["total_runs"] == 0
        assert s["avg_iterations"] == 0.0
        assert s["avg_duration_s"] == 0.0


class TestGetFailures:
    def test_returns_failed_runs(self):
        conn = MagicMock()
        now = datetime.now(timezone.utc)
        conn.execute.return_value.fetchall.return_value = [
            ("id-f1", "task-f1", now, now, False, 15, 30, 0, 3, 0, True,
             "Max iterations", "qwen", 8000, 6000),
        ]
        results = get_failures(conn, days=30)
        assert len(results) == 1
        assert results[0]["success"] is False
        assert results[0]["failure_reason"] == "Max iterations"

    def test_no_failures(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        results = get_failures(conn, days=7)
        assert results == []


# ── Orchestrator Integration Tests (mock store) ──────────────────────────────

class TestWriteRunMetrics:
    def test_write_metrics_called_from_orchestrator(self):
        """Verify _write_run_metrics calls write_metrics with correct args."""
        from mca.orchestrator.loop import _write_run_metrics
        now = datetime.now(timezone.utc)
        mock_store = MagicMock()
        mock_client = MagicMock()
        mock_client.token_usage = {"prompt_tokens": 1000, "completion_tokens": 500}

        with patch("mca.memory.metrics.write_metrics") as mock_write:
            mock_write.return_value = "metric-xyz"
            _write_run_metrics(
                mock_store,
                task_id="task-123",
                started_at=now,
                success=True,
                iterations=5,
                tool_calls=10,
                files_changed=2,
                tests_runs=1,
                lint_runs=0,
                rollback_used=False,
                failure_reason=None,
                model="qwen-72b",
                client=mock_client,
            )
            mock_write.assert_called_once()
            kwargs = mock_write.call_args[1]
            assert kwargs["task_id"] == "task-123"
            assert kwargs["success"] is True
            assert kwargs["token_prompt"] == 1000
            assert kwargs["token_completion"] == 500

    def test_write_metrics_skips_without_store(self):
        """_write_run_metrics should silently skip when store is None."""
        from mca.orchestrator.loop import _write_run_metrics
        now = datetime.now(timezone.utc)
        # Should not raise
        _write_run_metrics(
            None,
            task_id="task-123",
            started_at=now,
            success=True,
            iterations=5,
            tool_calls=10,
            files_changed=2,
            tests_runs=1,
            lint_runs=0,
            rollback_used=False,
            failure_reason=None,
            model="qwen-72b",
            client=None,
        )

    def test_write_metrics_handles_exception(self):
        """_write_run_metrics should not raise even if write_metrics fails."""
        from mca.orchestrator.loop import _write_run_metrics
        now = datetime.now(timezone.utc)
        mock_store = MagicMock()
        mock_store.conn = MagicMock()

        with patch("mca.memory.metrics.write_metrics", side_effect=Exception("DB error")):
            # Should not raise
            _write_run_metrics(
                mock_store,
                task_id="task-123",
                started_at=now,
                success=False,
                iterations=15,
                tool_calls=30,
                files_changed=0,
                tests_runs=3,
                lint_runs=0,
                rollback_used=True,
                failure_reason="Max iterations",
                model="qwen-72b",
                client=None,
            )
