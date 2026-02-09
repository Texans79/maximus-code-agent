"""Tests for knowledge graph — builder + store + recall."""
import os
import textwrap
from pathlib import Path

import pytest

from mca.memory.graph_builder import (
    GraphData,
    GraphNode,
    build_graph,
    extract_dependencies,
    extract_js_ts,
    extract_python,
    walk_workspace,
)
from mca.memory.recall import _extract_keywords


# ── Graph Builder Tests (always run, no external deps) ────────────────────


@pytest.fixture
def python_project(tmp_path):
    """Create a minimal Python project for testing."""
    (tmp_path / "main.py").write_text(textwrap.dedent("""\
        import os
        from pathlib import Path
        from mylib import helper

        class App:
            def run(self):
                helper.do_stuff()

        def main():
            app = App()
            app.run()
    """))
    (tmp_path / "mylib").mkdir()
    (tmp_path / "mylib" / "__init__.py").write_text("")
    (tmp_path / "mylib" / "helper.py").write_text(textwrap.dedent("""\
        from base import BaseClass

        class Helper(BaseClass):
            def do_stuff(self):
                return 42
    """))
    (tmp_path / "requirements.txt").write_text("flask>=2.0\nrequests\n")
    return tmp_path


@pytest.fixture
def js_project(tmp_path):
    (tmp_path / "index.js").write_text(textwrap.dedent("""\
        import express from 'express';
        const lodash = require('lodash');

        export function startServer() { }
        export class Router { }
    """))
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"express":"^4"},"devDependencies":{"jest":"^29"}}'
    )
    return tmp_path


class TestWalkWorkspace:
    def test_walks_files(self, python_project):
        files = walk_workspace(python_project)
        names = [str(f) for f in files]
        assert "main.py" in names
        assert any("helper.py" in n for n in names)

    def test_skips_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("x")
        (tmp_path / "app.py").write_text("x")
        files = walk_workspace(tmp_path)
        names = [str(f) for f in files]
        assert "app.py" in names
        assert not any(".git" in n for n in names)

    def test_skips_node_modules(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("x")
        (tmp_path / "app.js").write_text("x")
        files = walk_workspace(tmp_path)
        assert not any("node_modules" in str(f) for f in files)

    def test_empty_dir(self, tmp_path):
        files = walk_workspace(tmp_path)
        assert files == []


class TestExtractPython:
    def test_extracts_file_node(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        file_nodes = [n for n in data.nodes if n.node_type == "file"]
        assert len(file_nodes) == 1
        assert file_nodes[0].name == "main.py"

    def test_extracts_imports(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        import_edges = [e for e in data.edges if e.edge_type == "imports"]
        imported_modules = {e.target.name for e in import_edges}
        assert "os" in imported_modules
        assert "pathlib" in imported_modules
        assert "mylib" in imported_modules

    def test_extracts_classes(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        class_nodes = [n for n in data.nodes if n.node_type == "class"]
        assert any(n.name == "App" for n in class_nodes)

    def test_extracts_functions(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        func_nodes = [n for n in data.nodes if n.node_type == "function"]
        func_names = {n.name for n in func_nodes}
        assert "main" in func_names

    def test_extracts_class_methods(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        func_nodes = [n for n in data.nodes if n.node_type == "function"]
        assert any("App.run" in n.name for n in func_nodes)

    def test_extracts_extends(self, python_project):
        source = (python_project / "mylib" / "helper.py").read_text()
        data = extract_python(Path("mylib/helper.py"), source)
        extends = [e for e in data.edges if e.edge_type == "extends"]
        assert len(extends) == 1
        assert extends[0].target.name == "BaseClass"

    def test_extracts_contains(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        contains = [e for e in data.edges if e.edge_type == "contains"]
        assert len(contains) >= 2

    def test_handles_syntax_error(self):
        data = extract_python(Path("bad.py"), "def broken(:\n")
        assert len(data.nodes) == 0

    def test_extracts_line_numbers(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        func_nodes = [n for n in data.nodes if n.node_type == "function" and n.name == "main"]
        assert len(func_nodes) >= 1
        assert func_nodes[0].line_number is not None
        assert func_nodes[0].line_number > 0

    def test_extracts_calls(self, python_project):
        source = (python_project / "main.py").read_text()
        data = extract_python(Path("main.py"), source)
        call_edges = [e for e in data.edges if e.edge_type == "calls"]
        called = {e.target.name for e in call_edges}
        assert "App" in called


class TestExtractJsTs:
    def test_extracts_imports(self, js_project):
        source = (js_project / "index.js").read_text()
        data = extract_js_ts(Path("index.js"), source)
        import_edges = [e for e in data.edges if e.edge_type == "imports"]
        imported = {e.target.name for e in import_edges}
        assert "express" in imported
        assert "lodash" in imported

    def test_extracts_exports(self, js_project):
        source = (js_project / "index.js").read_text()
        data = extract_js_ts(Path("index.js"), source)
        contains = [e for e in data.edges if e.edge_type == "contains"]
        names = {e.target.name for e in contains}
        assert "startServer" in names
        assert "Router" in names


class TestExtractDependencies:
    def test_python_deps(self, python_project):
        data = extract_dependencies(python_project)
        dep_nodes = [n for n in data.nodes if n.node_type == "dependency"]
        dep_names = {n.name for n in dep_nodes}
        assert "flask" in dep_names
        assert "requests" in dep_names

    def test_js_deps(self, js_project):
        data = extract_dependencies(js_project)
        dep_nodes = [n for n in data.nodes if n.node_type == "dependency"]
        dep_names = {n.name for n in dep_nodes}
        assert "express" in dep_names
        assert "jest" in dep_names


class TestBuildGraph:
    def test_full_python_build(self, python_project):
        data = build_graph(python_project)
        assert len(data.nodes) > 0
        assert len(data.edges) > 0
        node_types = {n.node_type for n in data.nodes}
        assert "file" in node_types
        assert "function" in node_types
        assert "class" in node_types

    def test_full_js_build(self, js_project):
        data = build_graph(js_project)
        assert len(data.nodes) > 0

    def test_empty_workspace(self, tmp_path):
        data = build_graph(tmp_path)
        assert isinstance(data, GraphData)


# ── Keyword Extraction Tests ─────────────────────────────────────────────


class TestExtractKeywords:
    def test_filters_stop_words(self):
        keywords = _extract_keywords("fix the login endpoint handler")
        assert "the" not in keywords
        assert "login" in keywords
        assert "endpoint" in keywords
        assert "handler" in keywords

    def test_filters_short_words(self):
        keywords = _extract_keywords("add a db connection pool")
        assert "db" not in keywords
        assert "connection" in keywords
        assert "pool" in keywords

    def test_preserves_identifiers(self):
        keywords = _extract_keywords("refactor UserAuth class")
        assert "UserAuth" in keywords

    def test_deduplicates(self):
        keywords = _extract_keywords("login Login LOGIN handler")
        assert keywords.count("login") + keywords.count("Login") + keywords.count("LOGIN") == 1

    def test_empty_string(self):
        assert _extract_keywords("") == []


# ── PostgreSQL Graph Store Tests (require live DB) ────────────────────────


def _pg_dsn() -> str:
    return os.environ.get(
        "MCA_TEST_POSTGRES_DSN",
        "postgresql://maximus_user@localhost:5432/openwebui?options=-csearch_path%3Dmca,public",
    )


def _can_connect_pg() -> bool:
    try:
        import psycopg
        conn = psycopg.connect(_pg_dsn(), autocommit=True, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _can_connect_pg(), reason="PostgreSQL not available")


@pytest.fixture
def graph_store():
    from mca.memory.graph import GraphStore
    from mca.memory.pg_store import PgMemoryStore
    s = PgMemoryStore(_pg_dsn())
    gs = GraphStore(s.conn)
    yield gs
    try:
        s.conn.execute("DELETE FROM mca.graph_edges")
        s.conn.execute("DELETE FROM mca.graph_nodes")
    except Exception:
        pass
    s.close()


@pg
class TestGraphStoreBuild:
    def test_build_and_stats(self, graph_store, python_project):
        data = build_graph(python_project)
        result = graph_store.build_graph(str(python_project), data)
        assert result["nodes"] > 0
        assert result["edges"] > 0

        stats = graph_store.get_stats(str(python_project))
        assert stats["total_nodes"] == result["nodes"]
        assert stats["total_edges"] == result["edges"]

    def test_rebuild_replaces_data(self, graph_store, python_project):
        data = build_graph(python_project)
        r1 = graph_store.build_graph(str(python_project), data)
        r2 = graph_store.build_graph(str(python_project), data)
        assert r1["nodes"] == r2["nodes"]


@pg
class TestGraphStoreQuery:
    def test_query_node(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        results = graph_store.query_node(str(python_project), "main")
        assert len(results) >= 1
        assert any(r["name"] == "main" for r in results)

    def test_get_neighbors(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        nodes = graph_store.find_by_name(str(python_project), "main.py", node_type="file")
        assert len(nodes) >= 1

        neighbors = graph_store.get_neighbors(nodes[0]["id"])
        assert len(neighbors) > 0

    def test_find_by_name_exact(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        results = graph_store.find_by_name(str(python_project), "App")
        assert any(r["name"] == "App" for r in results)

    def test_find_by_name_with_type(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        results = graph_store.find_by_name(str(python_project), "App", node_type="class")
        assert all(r["node_type"] == "class" for r in results)


@pg
class TestGraphStoreTraversal:
    def test_traverse_depth_1(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        nodes = graph_store.find_by_name(str(python_project), "App", node_type="class")
        assert len(nodes) >= 1

        reachable = graph_store.traverse(nodes[0]["id"], max_depth=1)
        assert len(reachable) > 1

    def test_traverse_depth_0(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        nodes = graph_store.find_by_name(str(python_project), "App", node_type="class")
        reachable = graph_store.traverse(nodes[0]["id"], max_depth=0)
        assert len(reachable) == 1


@pg
class TestGraphRecall:
    def test_graph_recall_finds_context(self, graph_store, python_project):
        data = build_graph(python_project)
        graph_store.build_graph(str(python_project), data)

        from mca.memory.recall import graph_recall
        context = graph_recall(
            graph_store.conn,
            str(python_project),
            "fix the App class run method",
        )
        assert "App" in context or "run" in context

    def test_graph_recall_empty_workspace(self, graph_store):
        from mca.memory.recall import graph_recall
        context = graph_recall(
            graph_store.conn,
            "/nonexistent/workspace",
            "anything here",
        )
        assert context == ""
