"""Memory recall — pgvector similarity search for task context.

Used before planning (inject similar past work) and after completion
(store outcomes for future recall).
"""
from __future__ import annotations

from typing import Any

from mca.log import get_logger
from mca.memory.base import MemoryStore
from mca.memory.embeddings import Embedder

log = get_logger("memory.recall")


def recall_similar(
    store: MemoryStore,
    embedder: Embedder,
    query: str,
    limit: int = 5,
    project: str = "",
) -> list[dict[str, Any]]:
    """Embed a query and find similar knowledge entries via pgvector.

    Returns entries sorted by cosine similarity (highest first).
    Falls back to full-text search if vector search returns nothing.
    """
    try:
        embedding = embedder.embed(query)
        results = store.vector_search(embedding, limit=limit, project=project)
        if results:
            log.info("recall: %d vector matches for '%s'", len(results), query[:60])
            return results
    except Exception as e:
        log.warning("vector recall failed, falling back to FTS: %s", e)

    # Fallback: full-text search
    results = store.search(query, limit=limit, project=project)
    if results:
        log.info("recall: %d FTS matches for '%s'", len(results), query[:60])
    return results


def store_outcome(
    store: MemoryStore,
    embedder: Embedder,
    task_id: str,
    summary: str,
    outcome: str = "completed",
    diff: str = "",
    project: str = "",
) -> str:
    """Store a task outcome as knowledge for future recall.

    Called after task completion to build up the knowledge base with
    outcomes, patterns, and lessons learned.
    """
    content = f"[{outcome}] {summary}"
    if diff:
        # Include a truncated diff for context
        content += f"\n\nDiff:\n{diff[:2000]}"

    embedding = None
    try:
        embedding = embedder.embed(summary)
    except Exception as e:
        log.warning("embedding failed for outcome storage: %s", e)

    entry_id = store.add(
        content=content,
        tags=["task-outcome", outcome],
        project=project,
        category="context",
        metadata={"task_id": task_id, "outcome": outcome},
        embedding=embedding,
    )
    log.info("stored outcome %s for task %s", entry_id[:8], task_id[:8])
    return entry_id
