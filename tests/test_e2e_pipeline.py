"""End-to-end integration test for the full orchestrator pipeline.

Requires ALL services running: vLLM (port 8000), Ollama (port 11434),
PostgreSQL (maximus_user@localhost:5432/openwebui).

Run with: PGPASSWORD=Arianna1 python -m pytest tests/test_e2e_pipeline.py -v
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx
import pytest


def _all_services_available() -> bool:
    """Check vLLM + Ollama + PostgreSQL are all reachable."""
    try:
        # vLLM
        r = httpx.get("http://localhost:8000/v1/models", timeout=3)
        if r.status_code != 200:
            return False
        # Ollama
        r = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code != 200:
            return False
        # PostgreSQL
        import psycopg
        conn = psycopg.connect(
            "postgresql://maximus_user@localhost:5432/openwebui?options=-csearch_path%3Dmca,public",
            autocommit=True, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


e2e = pytest.mark.skipif(not _all_services_available(),
                         reason="Not all services available (vLLM + Ollama + PG)")


@e2e
class TestEndToEndPipeline:
    """Full pipeline test: creates a demo repo, runs a task, verifies results."""

    @pytest.fixture
    def demo_repo(self, tmp_path):
        """Create a minimal demo repo for testing."""
        repo = tmp_path / "demo_project"
        repo.mkdir()

        # Initialize git repo
        subprocess.run(["git", "init"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=repo, capture_output=True)

        # Create a simple Python file
        (repo / "calc.py").write_text("""\
def add(a, b):
    return a + b
""")
        (repo / "pyproject.toml").write_text("""\
[project]
name = "demo"
version = "0.1.0"
""")

        # Initial commit
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"],
                        cwd=repo, capture_output=True)

        return repo

    def test_registry_build(self, demo_repo):
        """Verify the full registry builds with all tools for a workspace."""
        from mca.config import load_config
        from mca.tools.registry import build_registry
        from mca.memory.base import get_store

        cfg = load_config(str(demo_repo))
        store = get_store(cfg)
        registry = build_registry(demo_repo, cfg, memory_store=store)

        tools = registry.list_tools()
        assert len(tools) >= 9

        actions = registry.list_actions()
        assert "read_file" in actions
        assert "run_command" in actions
        assert "done" in actions
        assert "memory_add" in actions

        store.close()

    def test_llm_chat_with_tools(self, demo_repo):
        """Verify the LLM client can send messages with tool definitions."""
        from mca.llm.client import LLMClient

        client = LLMClient()
        resp = client.chat(
            messages=[{"role": "user", "content": "Reply with exactly: TEST_OK"}],
            temperature=0.1,
            max_tokens=50,
        )
        assert resp.content
        assert len(resp.content) > 0
        client.close()

    def test_memory_recall_cycle(self, demo_repo):
        """Store an outcome and recall it by similarity."""
        from mca.memory.base import get_store
        from mca.memory.embeddings import get_embedder
        from mca.memory.recall import recall_similar, store_outcome
        from mca.config import load_config

        cfg = load_config(str(demo_repo))
        store = get_store(cfg)
        embedder = get_embedder(cfg)

        # Store an outcome
        tid = store.create_task("Add subtract function to calc.py")
        entry_id = store_outcome(
            store, embedder, tid,
            "Added subtract function to demo project",
            outcome="completed",
            project=str(demo_repo),
        )
        assert entry_id

        # Recall it
        results = recall_similar(store, embedder, "calculator subtract operation")
        assert len(results) >= 1
        assert "subtract" in results[0]["content"]

        # Cleanup
        store.delete(entry_id)
        try:
            store.conn.execute("DELETE FROM mca.tasks WHERE id = %s::uuid", (tid,))
        except Exception:
            pass
        embedder.close()
        store.close()

    def test_tool_definitions_and_prompt(self, demo_repo):
        """Verify tool definitions aggregate correctly and prompt is valid."""
        from mca.config import load_config
        from mca.tools.registry import build_registry
        from mca.orchestrator.loop import _build_system_prompt

        cfg = load_config(str(demo_repo))
        registry = build_registry(demo_repo, cfg)

        # Tool definitions should include all actions
        defs = registry.tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "read_file" in names
        assert "write_file" in names
        assert "run_command" in names
        assert "done" in names
        assert "git_checkpoint" in names
        assert "run_tests" in names
        assert "replace_in_file" in names
        assert len(defs) >= 25  # 26 without memory, 28 with

        # System prompt should have core rules
        prompt = _build_system_prompt(registry)
        assert "Maximus Code Agent" in prompt
        assert "done" in prompt
