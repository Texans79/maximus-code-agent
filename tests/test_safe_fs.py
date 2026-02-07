"""Tests for SafeFS workspace jail and diff application."""
import os
import tempfile
from pathlib import Path

import pytest

from mca.tools.safe_fs import SafeFS, WorkspaceViolation


@pytest.fixture
def workspace(tmp_path):
    """Create a temp workspace with some files."""
    (tmp_path / "hello.py").write_text("print('hello')\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.txt").write_text("line1\nline2\nline3\n")
    return tmp_path


@pytest.fixture
def fs(workspace):
    return SafeFS(workspace)


class TestJail:
    def test_read_inside(self, fs):
        assert "hello" in fs.read("hello.py")

    def test_read_subdir(self, fs):
        assert "line2" in fs.read("sub/data.txt")

    def test_traversal_blocked(self, fs):
        with pytest.raises(WorkspaceViolation):
            fs.read("../../../etc/passwd")

    def test_absolute_outside_blocked(self, fs):
        with pytest.raises(WorkspaceViolation):
            fs.read("/etc/passwd")

    def test_dotdot_in_middle(self, fs):
        with pytest.raises(WorkspaceViolation):
            fs.read("sub/../../etc/passwd")

    def test_symlink_escape(self, fs, workspace):
        # Create a symlink pointing outside workspace
        link = workspace / "escape"
        try:
            link.symlink_to("/etc")
            with pytest.raises(WorkspaceViolation):
                fs.read("escape/passwd")
        except OSError:
            pytest.skip("Cannot create symlink")

    def test_exists(self, fs):
        assert fs.exists("hello.py")
        assert not fs.exists("nope.py")


class TestWrite:
    def test_write_new(self, fs, workspace):
        fs.write("new.txt", "content")
        assert (workspace / "new.txt").read_text() == "content"

    def test_write_nested(self, fs, workspace):
        fs.write("a/b/c.txt", "deep")
        assert (workspace / "a" / "b" / "c.txt").read_text() == "deep"


class TestDiff:
    def test_generate_diff(self, fs):
        diff = fs.generate_diff("hello.py", "print('world')\n")
        assert "---" in diff
        assert "+++" in diff
        assert "-print('hello')" in diff
        assert "+print('world')" in diff

    def test_apply_diff(self, fs, workspace):
        diff = fs.generate_diff("hello.py", "print('world')\n")
        ok = fs.apply_diff("hello.py", diff)
        assert ok
        assert "world" in (workspace / "hello.py").read_text()


class TestSearch:
    def test_search_found(self, fs):
        results = fs.search("line2")
        assert len(results) == 1
        assert results[0]["file"] == os.path.join("sub", "data.txt")
        assert results[0]["line"] == 2

    def test_search_regex(self, fs):
        results = fs.search(r"line\d")
        assert len(results) == 3

    def test_search_not_found(self, fs):
        results = fs.search("nonexistent")
        assert results == []


class TestTree:
    def test_tree(self, fs):
        tree = fs.tree()
        assert any("hello.py" in t for t in tree)
        assert any("data.txt" in t for t in tree)
