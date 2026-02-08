"""Tests for DepDoctor tool."""
import pytest

from mca.tools.dep_doctor import DepDoctor
from mca.tools.safe_shell import SafeShell


@pytest.fixture
def python_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="test"\n')
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n")
    return tmp_path


@pytest.fixture
def node_workspace(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"test"}')
    (tmp_path / "node_modules").mkdir()
    return tmp_path


@pytest.fixture
def empty_workspace(tmp_path):
    return tmp_path


class TestPython:
    def test_detect_python(self, python_workspace):
        shell = SafeShell(python_workspace)
        doc = DepDoctor(shell, python_workspace)
        result = doc.execute("check_python", {})
        assert result.ok
        assert result.data["detected"] is True
        assert result.data["venv_found"] is True

    def test_no_python_project(self, empty_workspace):
        shell = SafeShell(empty_workspace)
        doc = DepDoctor(shell, empty_workspace)
        result = doc.execute("check_python", {})
        assert result.data["detected"] is False


class TestNode:
    def test_detect_node(self, node_workspace):
        shell = SafeShell(node_workspace)
        doc = DepDoctor(shell, node_workspace)
        result = doc.execute("check_node", {})
        assert result.ok
        assert result.data["detected"] is True
        assert result.data["node_modules"] is True

    def test_no_node_project(self, empty_workspace):
        shell = SafeShell(empty_workspace)
        doc = DepDoctor(shell, empty_workspace)
        result = doc.execute("check_node", {})
        assert result.data["detected"] is False


class TestFullCheck:
    def test_check_environment(self, python_workspace):
        shell = SafeShell(python_workspace)
        doc = DepDoctor(shell, python_workspace)
        result = doc.execute("check_environment", {})
        assert result.ok
        assert "python" in result.data["ecosystems"]

    def test_verify(self, python_workspace):
        shell = SafeShell(python_workspace)
        doc = DepDoctor(shell, python_workspace)
        assert doc.verify().ok
        assert "python" in doc.verify().data["ecosystems"]
