"""Tests for memory recall — unit tests with mocked embedder + live integration."""
import os

import pytest

from mca.memory.recall import recall_similar, store_outcome
from mca.memory.sqlite_store import SqliteMemoryStore


class FakeEmbedder:
    """Mock embedder that returns deterministic vectors."""

    def __init__(self, dim: int = 768):
        self._dim = dim
        self._call_count = 0

    def embed(self, text: str) -> list[float]:
        self._call_count += 1
        # Return a vector based on text hash for reproducibility
        h = hash(text) % 1000
        return [h / 1000.0] * self._dim

    def close(self):
        pass


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "recall_test.db"
    s = SqliteMemoryStore(db)
    yield s
    s.close()


@pytest.fixture
def embedder():
    return FakeEmbedder()


class TestRecallSimilar:
    def test_falls_back_to_fts(self, store, embedder):
        """SQLite has no vector search, should fall back to FTS."""
        store.add("PostgreSQL indexing strategies for performance")
        store.add("React hooks best practices")
        results = recall_similar(store, embedder, "PostgreSQL performance")
        assert len(results) >= 1
        assert "PostgreSQL" in results[0]["content"]

    def test_empty_store_returns_empty(self, store, embedder):
        results = recall_similar(store, embedder, "anything")
        assert results == []

    def test_limit_respected(self, store, embedder):
        for i in range(10):
            store.add(f"Python tip number {i} for coding")
        results = recall_similar(store, embedder, "Python coding", limit=3)
        assert len(results) <= 3


class TestStoreOutcome:
    def test_stores_completed_outcome(self, store, embedder):
        tid = store.create_task("Fix login bug")
        entry_id = store_outcome(store, embedder, tid, "Fixed authentication timeout",
                                 outcome="completed")
        entry = store.get(entry_id)
        assert entry is not None
        assert "[completed]" in entry["content"]
        assert "task-outcome" in entry["tags"]
        assert entry["category"] == "context"

    def test_stores_failed_outcome(self, store, embedder):
        tid = store.create_task("Refactor API")
        entry_id = store_outcome(store, embedder, tid, "Tests failed after refactor",
                                 outcome="failed")
        entry = store.get(entry_id)
        assert "[failed]" in entry["content"]
        assert "failed" in entry["tags"]

    def test_stores_diff(self, store, embedder):
        tid = store.create_task("Add feature")
        diff = "+def new_function():\n+    return 42"
        entry_id = store_outcome(store, embedder, tid, "Added new_function",
                                 diff=diff)
        entry = store.get(entry_id)
        assert "Diff:" in entry["content"]
        assert "new_function" in entry["content"]

    def test_outcome_metadata(self, store, embedder):
        tid = store.create_task("Test task")
        entry_id = store_outcome(store, embedder, tid, "Done",
                                 outcome="completed", project="/home/test")
        entry = store.get(entry_id)
        assert entry["metadata"]["task_id"] == tid
        assert entry["metadata"]["outcome"] == "completed"
        assert entry["project"] == "/home/test"


# ── Live PostgreSQL Integration Tests ────────────────────────────────────────

def _pg_dsn() -> str | None:
    dsn = os.environ.get("MCA_TEST_POSTGRES_DSN")
    if dsn:
        return dsn
    return "postgresql://maximus_user@localhost:5432/openwebui?options=-csearch_path%3Dmca,public"


def _can_connect_pg() -> bool:
    try:
        import psycopg
        conn = psycopg.connect(_pg_dsn(), autocommit=True, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def _ollama_available() -> bool:
    try:
        import httpx
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


pg_live = pytest.mark.skipif(
    not (_can_connect_pg() and _ollama_available()),
    reason="PostgreSQL + Ollama not both available",
)


@pytest.fixture
def pg_store():
    from mca.memory.pg_store import PgMemoryStore
    s = PgMemoryStore(_pg_dsn())
    yield s
    try:
        s.conn.execute("DELETE FROM mca.evaluations")
        s.conn.execute("DELETE FROM mca.tools")
        s.conn.execute("DELETE FROM mca.artifacts")
        s.conn.execute("DELETE FROM mca.steps")
        s.conn.execute("DELETE FROM mca.tasks")
        s.conn.execute("DELETE FROM mca.knowledge")
    except Exception:
        pass
    s.close()


@pytest.fixture
def live_embedder():
    from mca.memory.embeddings import Embedder
    e = Embedder()
    yield e
    e.close()


@pg_live
class TestLiveRecall:
    def test_vector_recall(self, pg_store, live_embedder):
        """Store entries with real embeddings, recall by similarity."""
        store_outcome(pg_store, live_embedder, "fake-task-1",
                      "Fixed PostgreSQL connection pooling issue",
                      outcome="completed")
        store_outcome(pg_store, live_embedder, "fake-task-2",
                      "Added React component for user dashboard",
                      outcome="completed")

        results = recall_similar(pg_store, live_embedder,
                                 "database connection problems")
        assert len(results) >= 1
        # PostgreSQL result should rank higher than React for DB query
        assert "PostgreSQL" in results[0]["content"]

    def test_recall_empty_db(self, pg_store, live_embedder):
        results = recall_similar(pg_store, live_embedder, "anything at all")
        assert results == []
