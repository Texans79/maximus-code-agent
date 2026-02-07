"""Tests for memory store (SQLite backend)."""
import pytest

from mca.memory.sqlite_store import SqliteMemoryStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_memory.db"
    s = SqliteMemoryStore(db)
    yield s
    s.close()


class TestSqliteStore:
    def test_add_and_get(self, store):
        mid = store.add("Remember to use pytest fixtures", tags=["testing"])
        entry = store.get(mid)
        assert entry is not None
        assert "pytest fixtures" in entry["content"]
        assert "testing" in entry["tags"]

    def test_search(self, store):
        store.add("Python async patterns for FastAPI")
        store.add("React component lifecycle hooks")
        store.add("Docker compose networking tips")

        results = store.search("Python FastAPI")
        assert len(results) >= 1
        assert "Python" in results[0]["content"] or "FastAPI" in results[0]["content"]

    def test_search_no_results(self, store):
        store.add("something about databases")
        results = store.search("quantum_physics_xyz")
        assert results == []

    def test_delete(self, store):
        mid = store.add("temporary note")
        assert store.get(mid) is not None
        assert store.delete(mid)
        assert store.get(mid) is None

    def test_list_recent(self, store):
        store.add("first")
        store.add("second")
        store.add("third")
        recent = store.list_recent(limit=2)
        assert len(recent) == 2
        # Most recent first
        assert "third" in recent[0]["content"]

    def test_tags_and_project(self, store):
        mid = store.add("note about vLLM", tags=["llm", "vllm"], project="/home/test")
        entry = store.get(mid)
        assert entry["tags"] == ["llm", "vllm"]
        assert entry["project"] == "/home/test"

    def test_metadata(self, store):
        mid = store.add("decision log", metadata={"decision": "use SQLite", "reason": "simplicity"})
        entry = store.get(mid)
        assert entry["metadata"]["decision"] == "use SQLite"
