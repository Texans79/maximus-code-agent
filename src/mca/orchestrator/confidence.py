"""Confidence scoring — pre-task intelligence for MCA.

Calculates a 0-100 confidence score based on:
  1. Similar task success rate (50%) — vector search for past outcomes
  2. Recent failure rate (20%) — last 10 run_metrics rows
  3. Novelty (30%) — how many similar tasks exist in memory

When confidence < SPIKE_THRESHOLD, MCA enters "spike mode":
  - System prompt adds caution instructions
  - auto mode upgrades to ask mode
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mca.log import get_logger

log = get_logger("confidence")

WEIGHTS = {"similar_success": 0.50, "failure_rate": 0.20, "novelty": 0.30}
SPIKE_THRESHOLD = 50


@dataclass
class ConfidenceScore:
    """Pre-task confidence assessment."""

    total: int  # 0-100 weighted composite
    similar_success: int  # 0-100 component
    failure_rate: int  # 0-100 component
    novelty: int  # 0-100 component
    similar_count: int  # number of similar tasks found
    recent_success_rate: float  # last 10 runs success %


def should_spike(score: ConfidenceScore) -> bool:
    """Return True if confidence is below the spike threshold."""
    return score.total < SPIKE_THRESHOLD


def calculate_confidence(
    store,
    embedder,
    task_description: str,
    limit: int = 5,
) -> ConfidenceScore:
    """Calculate a 0-100 confidence score for a task.

    Args:
        store: MemoryStore (pg_store) with vector_search and conn
        embedder: Embedder instance for creating embeddings
        task_description: The task to score
        limit: Max similar outcomes to retrieve

    Returns:
        ConfidenceScore with total and component scores
    """
    # 1. Find similar past outcomes via vector search
    outcomes = _find_similar_outcomes(store, embedder, task_description, limit)
    similar_count = len(outcomes)

    # 2. Score components
    sim_score = _similar_success_score(outcomes)
    fail_score = _failure_rate_score(store)
    nov_score = _novelty_score(similar_count)

    # 3. Recent success rate (for reporting, not scoring)
    recent_rate = _recent_success_rate(store)

    # 4. Weighted total, clamped 0-100
    total = int(
        sim_score * WEIGHTS["similar_success"]
        + fail_score * WEIGHTS["failure_rate"]
        + nov_score * WEIGHTS["novelty"]
    )
    total = max(0, min(100, total))

    score = ConfidenceScore(
        total=total,
        similar_success=sim_score,
        failure_rate=fail_score,
        novelty=nov_score,
        similar_count=similar_count,
        recent_success_rate=recent_rate,
    )
    log.info(
        "confidence=%d (sim=%d fail=%d nov=%d) similar=%d recent_rate=%.0f%%",
        total, sim_score, fail_score, nov_score, similar_count, recent_rate * 100,
    )
    return score


def _find_similar_outcomes(
    store, embedder, task_description: str, limit: int,
) -> list[dict[str, Any]]:
    """Embed the task and search for similar past outcomes."""
    try:
        embedding = embedder.embed(task_description)
        results = store.vector_search(embedding, limit=limit)
        # Filter to only task-outcome entries
        return [r for r in results if "task-outcome" in r.get("tags", [])]
    except Exception as e:
        log.debug("similar outcome search failed: %s", e)
        return []


def _similar_success_score(outcomes: list[dict[str, Any]]) -> int:
    """Score 0-100 based on success rate of similar past tasks.

    No similar tasks → 30 (neutral-low, unknown territory).
    """
    if not outcomes:
        return 30

    completed = sum(
        1 for o in outcomes if "completed" in o.get("tags", [])
    )
    total = len(outcomes)
    return int((completed / total) * 100)


def _failure_rate_score(store) -> int:
    """Score 0-100 based on recent run success rate.

    Queries last 10 run_metrics rows. No history → 50 (neutral).
    """
    try:
        rows = store.conn.execute(
            "SELECT success FROM mca.run_metrics ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        if not rows:
            return 50
        successes = sum(1 for r in rows if r[0])
        return int((successes / len(rows)) * 100)
    except Exception as e:
        log.debug("failure rate query failed: %s", e)
        return 50


def _novelty_score(similar_count: int) -> int:
    """Score 0-100 based on how many similar tasks exist.

    More prior art = higher confidence:
      3+ matches → 80
      1-2 matches → 50
      0 matches → 20
    """
    if similar_count >= 3:
        return 80
    if similar_count >= 1:
        return 50
    return 20


def _recent_success_rate(store) -> float:
    """Get the success rate of last 10 runs as a float 0.0-1.0."""
    try:
        rows = store.conn.execute(
            "SELECT success FROM mca.run_metrics ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        if not rows:
            return 0.0
        return sum(1 for r in rows if r[0]) / len(rows)
    except Exception:
        return 0.0
