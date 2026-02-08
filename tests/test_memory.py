"""Tests for memory stores — SQLite fallback + PostgreSQL integration.

SQLite tests always run (no external dependencies).
PostgreSQL tests require a live database and are marked with @pytest.mark.pg.
Run them with: pytest -m pg
"""
import os

import pytest

from mca.memory.sqlite_store import SqliteMemoryStore


# ── SQLite Fallback Tests (always run) ───────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_memory.db"
    s = SqliteMemoryStore(db)
    yield s
    s.close()


class TestSqliteKnowledge:
    def test_backend_properties(self, store):
        assert store.backend_name == "sqlite"
        assert store.is_fallback is True

    def test_add_and_get(self, store):
        mid = store.add("Remember to use pytest fixtures", tags=["testing"], category="recipe")
        entry = store.get(mid)
        assert entry is not None
        assert "pytest fixtures" in entry["content"]
        assert "testing" in entry["tags"]
        assert entry["category"] == "recipe"

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
        assert "third" in recent[0]["content"]

    def test_tags_and_project(self, store):
        mid = store.add("note about vLLM", tags=["llm", "vllm"], project="/home/test")
        entry = store.get(mid)
        assert entry["tags"] == ["llm", "vllm"]
        assert entry["project"] == "/home/test"

    def test_metadata(self, store):
        mid = store.add("decision log", metadata={"decision": "use postgres", "reason": "durability"})
        entry = store.get(mid)
        assert entry["metadata"]["decision"] == "use postgres"

    def test_category_default(self, store):
        mid = store.add("uncategorized note")
        entry = store.get(mid)
        assert entry["category"] == "general"

    def test_vector_search_returns_empty(self, store):
        results = store.vector_search([0.1] * 384, limit=5)
        assert results == []


class TestSqliteTasks:
    def test_create_and_get_task(self, store):
        tid = store.create_task("Fix the login bug", workspace="/home/test/project")
        task = store.get_task(tid)
        assert task is not None
        assert task["description"] == "Fix the login bug"
        assert task["status"] == "pending"
        assert task["workspace"] == "/home/test/project"

    def test_update_task_status(self, store):
        tid = store.create_task("Refactor auth module")
        store.update_task(tid, status="running")
        task = store.get_task(tid)
        assert task["status"] == "running"

    def test_update_task_result(self, store):
        tid = store.create_task("Run tests")
        store.update_task(tid, status="completed", result={"passed": 42, "failed": 0})
        task = store.get_task(tid)
        assert task["status"] == "completed"
        assert task["result"]["passed"] == 42


class TestSqliteSteps:
    def test_add_step(self, store):
        tid = store.create_task("Multi-step task")
        s1 = store.add_step(tid, "Plan the implementation", agent_role="planner")
        s2 = store.add_step(tid, "Write the code", agent_role="implementer")
        assert s1 != s2

    def test_update_step(self, store):
        tid = store.create_task("Step test")
        sid = store.add_step(tid, "Run tests")
        store.update_step(sid, status="completed", duration_ms=1500)


class TestSqliteArtifacts:
    def test_add_artifact(self, store):
        tid = store.create_task("Artifact test")
        aid = store.add_artifact(tid, "/src/main.py", "modified", diff="@@ -1 +1 @@")
        assert aid


class TestSqliteTools:
    def test_log_tool(self, store):
        tid = store.create_task("Tool test")
        lid = store.log_tool(tid, "bash", command="pytest", exit_code=0, stdout="OK", duration_ms=3000)
        assert lid

    def test_log_tool_no_task(self, store):
        lid = store.log_tool(None, "bash", command="ls -la")
        assert lid


class TestSqliteEvaluations:
    def test_add_evaluation(self, store):
        tid = store.create_task("Eval test")
        eid = store.add_evaluation(tid, "approve", evaluator="reviewer", comments="Looks good")
        assert eid


# ── PostgreSQL Integration Tests (require live database) ─────────────────────

def _pg_dsn() -> str | None:
    """Get PostgreSQL DSN from env or default."""
    dsn = os.environ.get("MCA_TEST_POSTGRES_DSN")
    if dsn:
        return dsn
    dsn = os.environ.get("DATABASE_URL")
    if dsn and "postgresql" in dsn:
        return dsn
    # Try default for this machine
    return "postgresql://maximus_user@localhost:5432/openwebui?options=-csearch_path%3Dmca,public"


def _can_connect_pg() -> bool:
    """Check if PostgreSQL is reachable."""
    try:
        import psycopg
        conn = psycopg.connect(_pg_dsn(), autocommit=True, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _can_connect_pg(), reason="PostgreSQL not available")


@pytest.fixture
def pg_store():
    from mca.memory.pg_store import PgMemoryStore
    s = PgMemoryStore(_pg_dsn())
    yield s
    # Clean up test data
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


@pg
class TestPgConnection:
    def test_backend_properties(self, pg_store):
        assert pg_store.backend_name == "postgres"
        assert pg_store.is_fallback is False

    def test_schema_exists(self, pg_store):
        row = pg_store.conn.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'mca'"
        ).fetchone()
        assert row is not None

    def test_tables_exist(self, pg_store):
        row = pg_store.conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'mca'"
        ).fetchone()
        assert row[0] >= 7  # migrations, tasks, steps, artifacts, knowledge, tools, evaluations


@pg
class TestPgKnowledge:
    def test_add_and_get(self, pg_store):
        mid = pg_store.add("PostgreSQL is the primary backend", tags=["db", "postgres"], category="decision")
        entry = pg_store.get(mid)
        assert entry is not None
        assert "PostgreSQL" in entry["content"]
        assert "postgres" in entry["tags"]
        assert entry["category"] == "decision"

    def test_search_fts(self, pg_store):
        pg_store.add("vLLM tensor parallel inference optimization")
        pg_store.add("React component rendering lifecycle")
        results = pg_store.search("vLLM inference")
        assert len(results) >= 1
        assert "vLLM" in results[0]["content"]

    def test_delete(self, pg_store):
        mid = pg_store.add("ephemeral note")
        assert pg_store.delete(mid)
        assert pg_store.get(mid) is None

    def test_list_recent(self, pg_store):
        pg_store.add("first entry")
        pg_store.add("second entry")
        recent = pg_store.list_recent(limit=2)
        assert len(recent) == 2
        assert "second" in recent[0]["content"]


@pg
class TestPgVectorSearch:
    def test_insert_and_search_embedding(self, pg_store):
        # Create directionally distinct embeddings (384 dims)
        # emb_a: high values in first half, low in second
        emb_a = [0.9] * 192 + [0.1] * 192
        # emb_b: low values in first half, high in second (opposite direction)
        emb_b = [0.1] * 192 + [0.9] * 192
        # query: very similar to emb_a
        emb_query = [0.85] * 192 + [0.15] * 192

        pg_store.add("entry close to query", embedding=emb_a)
        pg_store.add("entry far from query", embedding=emb_b)

        results = pg_store.vector_search(emb_query, limit=2)
        assert len(results) == 2
        # The entry with emb_a should be most similar
        assert "close to query" in results[0]["content"]
        assert "similarity" in results[0]
        assert results[0]["similarity"] > results[1]["similarity"]

    def test_vector_search_with_project_filter(self, pg_store):
        emb = [0.5] * 384
        pg_store.add("project A note", embedding=emb, project="proj-a")
        pg_store.add("project B note", embedding=emb, project="proj-b")

        results = pg_store.vector_search(emb, limit=10, project="proj-a")
        assert all(r["project"] == "proj-a" for r in results)


@pg
class TestPgTasks:
    def test_create_and_get_task(self, pg_store):
        tid = pg_store.create_task("Implement feature X", workspace="/home/test")
        task = pg_store.get_task(tid)
        assert task["description"] == "Implement feature X"
        assert task["status"] == "pending"

    def test_task_lifecycle(self, pg_store):
        tid = pg_store.create_task("Full lifecycle task")
        pg_store.update_task(tid, status="running")
        assert pg_store.get_task(tid)["status"] == "running"

        pg_store.update_task(tid, status="completed", result={"files_modified": 3})
        task = pg_store.get_task(tid)
        assert task["status"] == "completed"
        assert task["result"]["files_modified"] == 3


@pg
class TestPgStepsAndArtifacts:
    def test_step_chain(self, pg_store):
        tid = pg_store.create_task("Chain test")
        s1 = pg_store.add_step(tid, "Plan", agent_role="planner")
        s2 = pg_store.add_step(tid, "Implement", agent_role="implementer")
        pg_store.update_step(s1, status="completed", duration_ms=500)
        pg_store.update_step(s2, status="completed", output={"files": ["main.py"]})
        assert s1 != s2

    def test_artifact(self, pg_store):
        tid = pg_store.create_task("Artifact test")
        aid = pg_store.add_artifact(tid, "src/app.py", "modified", diff="+line")
        assert aid

    def test_tool_log(self, pg_store):
        tid = pg_store.create_task("Tool test")
        lid = pg_store.log_tool(tid, "bash", command="pytest", exit_code=0, duration_ms=2000)
        assert lid

    def test_evaluation(self, pg_store):
        tid = pg_store.create_task("Eval test")
        eid = pg_store.add_evaluation(
            tid, "approve", evaluator="reviewer",
            issues=[], comments="Clean implementation"
        )
        assert eid


# ── Fallback Behavior Tests ──────────────────────────────────────────────────

class TestFallbackBehavior:
    def test_get_store_returns_sqlite_on_bad_dsn(self):
        """When PG is unreachable, get_store should fall back to SQLite."""
        from mca.config import Config
        from mca.memory.base import get_store

        bad_cfg = Config({
            "memory": {
                "backend": "postgres",
                "postgres_dsn": "postgresql://nobody:wrong@localhost:59999/nonexistent",
                "sqlite_path": "/tmp/test_fallback.db",
            }
        })
        # Temporarily clear env vars that _resolve_dsn checks
        saved = {}
        for key in ("DATABASE_URL", "MCA_MEMORY_POSTGRES_DSN", "PGPASSWORD", "PGHOST"):
            if key in os.environ:
                saved[key] = os.environ.pop(key)

        try:
            store = get_store(bad_cfg)
            assert store.backend_name == "sqlite"
            assert store.is_fallback is True
            store.close()
        finally:
            os.environ.update(saved)
            import pathlib
            pathlib.Path("/tmp/test_fallback.db").unlink(missing_ok=True)
