"""Tests for RepoIndexer tool."""
import json

import pytest

from mca.tools.repo_indexer import RepoIndexer


@pytest.fixture
def python_workspace(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')\n")
    (tmp_path / "app.py").write_text("from flask import Flask\n")
    (tmp_path / "requirements.txt").write_text("flask>=2.0\nrequests\nnumpy>=1.24\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test"\ndependencies = [\n  "typer>=0.9",\n  "rich",\n]\n'
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.py").write_text("x = 1\n")
    return tmp_path


@pytest.fixture
def node_workspace(tmp_path):
    (tmp_path / "index.js").write_text("console.log('hi')\n")
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "test",
        "dependencies": {"express": "^4.0", "lodash": "^4.17"},
        "devDependencies": {"jest": "^29"},
    }))
    return tmp_path


@pytest.fixture
def empty_workspace(tmp_path):
    return tmp_path


class TestEntrypoints:
    def test_find_python_entrypoints(self, python_workspace):
        indexer = RepoIndexer(python_workspace)
        result = indexer.execute("find_entrypoints", {})
        assert result.ok
        entries = result.data["entrypoints"]
        assert "main.py" in entries
        assert "app.py" in entries

    def test_find_node_entrypoints(self, node_workspace):
        indexer = RepoIndexer(node_workspace)
        result = indexer.execute("find_entrypoints", {})
        assert "index.js" in result.data["entrypoints"]

    def test_empty_workspace(self, empty_workspace):
        indexer = RepoIndexer(empty_workspace)
        result = indexer.execute("find_entrypoints", {})
        assert result.ok
        assert result.data["entrypoints"] == []


class TestDependencies:
    def test_parse_requirements_txt(self, python_workspace):
        indexer = RepoIndexer(python_workspace)
        result = indexer.execute("parse_dependencies", {})
        assert result.ok
        reqs = result.data["dependencies"]["requirements.txt"]
        assert "flask" in reqs
        assert "requests" in reqs
        assert "numpy" in reqs

    def test_parse_pyproject(self, python_workspace):
        indexer = RepoIndexer(python_workspace)
        result = indexer.execute("parse_dependencies", {})
        deps = result.data["dependencies"]["pyproject.toml"]
        assert "typer" in deps
        assert "rich" in deps

    def test_parse_package_json(self, node_workspace):
        indexer = RepoIndexer(node_workspace)
        result = indexer.execute("parse_dependencies", {})
        deps = result.data["dependencies"]["package.json"]
        assert "express" in deps["dependencies"]
        assert "jest" in deps["devDependencies"]


class TestFullIndex:
    def test_index_repo(self, python_workspace):
        indexer = RepoIndexer(python_workspace)
        result = indexer.execute("index_repo", {})
        assert result.ok
        assert "entrypoints" in result.data
        assert "dependencies" in result.data
        assert "file_types" in result.data
        assert ".py" in result.data["file_types"]

    def test_verify(self, python_workspace):
        indexer = RepoIndexer(python_workspace)
        assert indexer.verify().ok
