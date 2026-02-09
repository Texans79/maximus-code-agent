"""Memory recall — pgvector similarity search + knowledge graph context.

Used before planning (inject similar past work + code structure) and
after completion (store outcomes for future recall).
"""
from __future__ import annotations

import re
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


# ── Graph recall ──────────────────────────────────────────────────────────

_STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "to", "of",
    "in", "for", "on", "with", "at", "by", "from", "up", "about", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "and", "but", "or", "not", "no", "nor", "if", "then", "else",
    "this", "that", "these", "those", "it", "its", "fix", "add", "update",
    "create", "make", "implement", "change", "modify", "refactor", "test",
    "run", "build", "check", "new", "old", "current", "existing", "all",
    "some", "any", "each", "every", "file", "code", "project", "function",
}


def _extract_keywords(text: str) -> list[str]:
    """Extract likely code identifiers from a task description.

    Keeps words that look like identifiers (3+ chars, not stop words).
    """
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text)
    keywords = []
    seen: set[str] = set()
    for word in words:
        lower = word.lower()
        if lower in _STOP_WORDS or len(word) < 3 or lower in seen:
            continue
        seen.add(lower)
        keywords.append(word)
    return keywords


def graph_recall(
    conn,
    workspace: str,
    task_description: str,
    max_nodes: int = 10,
) -> str:
    """Extract graph context relevant to a task description.

    1. Tokenize task into keywords
    2. Search graph_nodes for matches
    3. Get neighbors for top matches
    4. Format as structured string for LLM injection

    Returns formatted string, or empty string if nothing found.
    """
    from mca.memory.graph import GraphStore

    graph = GraphStore(conn)
    keywords = _extract_keywords(task_description)
    if not keywords:
        return ""

    matched_nodes: list[dict] = []
    seen_ids: set[str] = set()

    for keyword in keywords[:5]:
        try:
            nodes = graph.find_by_name(workspace, keyword)
            for node in nodes:
                if node["id"] not in seen_ids:
                    seen_ids.add(node["id"])
                    matched_nodes.append(node)
        except Exception:
            continue

    if not matched_nodes:
        return ""

    context_parts: list[str] = []
    for node in matched_nodes[:3]:
        loc = ""
        if node.get("file_path"):
            loc = f" ({node['file_path']}"
            if node.get("line_number"):
                loc += f":{node['line_number']}"
            loc += ")"
        context_parts.append(f"- {node['node_type']} '{node['name']}'{loc}")

        try:
            neighbors = graph.get_neighbors(node["id"], limit=10)
            for nb in neighbors:
                if nb["id"] not in seen_ids:
                    seen_ids.add(nb["id"])
                    nb_loc = f" ({nb['file_path']})" if nb.get("file_path") else ""
                    context_parts.append(
                        f"  {nb['direction']} {nb['edge_type']} -> "
                        f"{nb['node_type']} '{nb['name']}'{nb_loc}"
                    )
        except Exception:
            continue

    if not context_parts:
        return ""

    result = "\n\nRelevant code structure:\n" + "\n".join(context_parts[:max_nodes * 3])
    log.info("graph_recall: %d keywords, %d matches, %d context lines",
             len(keywords), len(matched_nodes), len(context_parts))
    return result
