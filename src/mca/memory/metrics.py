"""Run metrics — write and query MCA run telemetry from mca.run_metrics."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mca.log import get_logger

log = get_logger("metrics")


def write_metrics(conn, *, task_id: str | None, started_at: datetime,
                  ended_at: datetime, success: bool, iterations: int,
                  tool_calls: int, files_changed: int, tests_runs: int,
                  lint_runs: int, rollback_used: bool,
                  failure_reason: str | None, model: str | None,
                  token_prompt: int, token_completion: int) -> str:
    """Insert a run_metrics row. Returns the row id."""
    row = conn.execute(
        """\
        INSERT INTO mca.run_metrics
            (task_id, started_at, ended_at, success, iterations, tool_calls,
             files_changed, tests_runs, lint_runs, rollback_used,
             failure_reason, model, token_prompt, token_completion)
        VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text
        """,
        (task_id, started_at, ended_at, success, iterations, tool_calls,
         files_changed, tests_runs, lint_runs, rollback_used,
         failure_reason, model, token_prompt, token_completion),
    ).fetchone()
    mid = row[0]
    log.info("wrote run_metrics %s (success=%s, iters=%d)", mid[:8], success, iterations)
    return mid


def get_last(conn, limit: int = 1) -> list[dict[str, Any]]:
    """Get the most recent N run metrics."""
    rows = conn.execute(
        """\
        SELECT id::text, task_id::text, started_at, ended_at, success,
               iterations, tool_calls, files_changed, tests_runs, lint_runs,
               rollback_used, failure_reason, model, token_prompt, token_completion
        FROM mca.run_metrics
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_summary(conn, days: int = 7) -> dict[str, Any]:
    """Aggregate metrics over the last N days."""
    row = conn.execute(
        """\
        SELECT
            COUNT(*)                                    AS total_runs,
            COUNT(*) FILTER (WHERE success)             AS successes,
            COUNT(*) FILTER (WHERE NOT success)         AS failures,
            ROUND(100.0 * COUNT(*) FILTER (WHERE success) / GREATEST(COUNT(*), 1), 1)
                                                        AS success_rate,
            ROUND(AVG(iterations)::numeric, 1)          AS avg_iterations,
            ROUND(AVG(tool_calls)::numeric, 1)          AS avg_tool_calls,
            SUM(token_prompt)                           AS total_prompt_tokens,
            SUM(token_completion)                       AS total_completion_tokens,
            ROUND(AVG(EXTRACT(EPOCH FROM ended_at - started_at))::numeric, 1)
                                                        AS avg_duration_s,
            SUM(tests_runs)                             AS total_test_runs,
            SUM(lint_runs)                              AS total_lint_runs,
            COUNT(*) FILTER (WHERE rollback_used)       AS rollback_count
        FROM mca.run_metrics
        WHERE started_at >= NOW() - make_interval(days => %s)
        """,
        (days,),
    ).fetchone()
    return {
        "days": days,
        "total_runs": int(row[0]),
        "successes": int(row[1]),
        "failures": int(row[2]),
        "success_rate": float(row[3]),
        "avg_iterations": float(row[4]) if row[4] else 0.0,
        "avg_tool_calls": float(row[5]) if row[5] else 0.0,
        "total_prompt_tokens": int(row[6] or 0),
        "total_completion_tokens": int(row[7] or 0),
        "avg_duration_s": float(row[8]) if row[8] else 0.0,
        "total_test_runs": int(row[9] or 0),
        "total_lint_runs": int(row[10] or 0),
        "rollback_count": int(row[11]),
    }


def get_failures(conn, days: int = 30) -> list[dict[str, Any]]:
    """Get failed runs in the last N days."""
    rows = conn.execute(
        """\
        SELECT id::text, task_id::text, started_at, ended_at, success,
               iterations, tool_calls, files_changed, tests_runs, lint_runs,
               rollback_used, failure_reason, model, token_prompt, token_completion
        FROM mca.run_metrics
        WHERE NOT success
          AND started_at >= NOW() - make_interval(days => %s)
        ORDER BY started_at DESC
        """,
        (days,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "task_id": row[1],
        "started_at": str(row[2]),
        "ended_at": str(row[3]),
        "success": row[4],
        "iterations": row[5],
        "tool_calls": row[6],
        "files_changed": row[7],
        "tests_runs": row[8],
        "lint_runs": row[9],
        "rollback_used": row[10],
        "failure_reason": row[11],
        "model": row[12],
        "token_prompt": row[13],
        "token_completion": row[14],
    }
