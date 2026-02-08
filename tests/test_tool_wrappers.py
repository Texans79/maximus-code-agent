"""Tests for tool wrapper adapters: FSTool, ShellTool, GitTool."""
import subprocess

import pytest

from mca.tools.fs_tool import FSTool
from mca.tools.git_tool import GitTool
from mca.tools.shell_tool import ShellTool
from mca.tools.safe_fs import SafeFS
from mca.tools.safe_shell import SafeShell
from mca.tools.git_ops import GitOps


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "hello.txt").write_text("Hello world\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')\n")
    return tmp_path


@pytest.fixture
def fs_tool(workspace):
    return FSTool(SafeFS(workspace))


@pytest.fixture
def shell_tool(workspace):
    return ShellTool(SafeShell(workspace))


@pytest.fixture
def git_workspace(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


@pytest.fixture
def git_tool(git_workspace):
    return GitTool(GitOps(git_workspace))


class TestFSTool:
    def test_read_file(self, fs_tool):
        result = fs_tool.execute("read_file", {"path": "hello.txt"})
        assert result.ok
        assert "Hello world" in result.data["content"]

    def test_write_file(self, fs_tool, workspace):
        result = fs_tool.execute("write_file", {"path": "new.txt", "content": "new content"})
        assert result.ok
        assert (workspace / "new.txt").read_text() == "new content"

    def test_list_files(self, fs_tool):
        result = fs_tool.execute("list_files", {})
        assert result.ok
        assert "hello.txt" in result.data["files"]

    def test_search(self, fs_tool):
        result = fs_tool.execute("search", {"pattern": "hello"})
        assert result.ok
        assert len(result.data["matches"]) >= 1

    def test_verify(self, fs_tool):
        result = fs_tool.verify()
        assert result.ok

    def test_unknown_action(self, fs_tool):
        with pytest.raises(ValueError):
            fs_tool.execute("bad_action", {})


class TestShellTool:
    def test_run_command(self, shell_tool):
        result = shell_tool.execute("run_command", {"cmd": "echo hello"})
        assert result.ok
        assert "hello" in result.data["stdout"]

    def test_run_failing_command(self, shell_tool):
        result = shell_tool.execute("run_command", {"cmd": "false"})
        assert not result.ok
        assert result.data["exit_code"] != 0

    def test_verify(self, shell_tool):
        result = shell_tool.verify()
        assert result.ok


class TestGitTool:
    def test_checkpoint(self, git_tool):
        result = git_tool.execute("git_checkpoint", {"message": "test checkpoint"})
        assert result.ok
        assert "mca-checkpoint" in result.data["tag"]

    def test_diff(self, git_tool):
        result = git_tool.execute("git_diff", {})
        assert result.ok

    def test_log(self, git_tool):
        result = git_tool.execute("git_log", {"n": 5})
        assert result.ok
        assert len(result.data["log"]) >= 1

    def test_verify(self, git_tool):
        result = git_tool.verify()
        assert result.ok
        assert result.data["is_repo"] is True
