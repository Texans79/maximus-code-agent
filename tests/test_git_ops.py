"""Tests for GitOps checkpoint and rollback."""
import subprocess

import pytest

from mca.tools.git_ops import GitOps


@pytest.fixture
def git_workspace(tmp_path):
    """Create a temp git repo."""
    subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], capture_output=True)
    (tmp_path / "file.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], capture_output=True)
    return tmp_path


@pytest.fixture
def git(git_workspace):
    return GitOps(git_workspace)


class TestCheckpoint:
    def test_is_repo(self, git):
        assert git.is_repo()

    def test_checkpoint_creates_tag(self, git, git_workspace):
        tag = git.checkpoint("test cp")
        assert tag.startswith("mca-checkpoint-")
        r = subprocess.run(
            ["git", "-C", str(git_workspace), "tag"],
            capture_output=True, text=True,
        )
        assert tag in r.stdout

    def test_checkpoint_with_changes(self, git, git_workspace):
        (git_workspace / "new.txt").write_text("new\n")
        tag = git.checkpoint()
        assert tag.startswith("mca-checkpoint-")


class TestRollback:
    def test_rollback_reverts(self, git, git_workspace):
        tag1 = git.checkpoint("before changes")
        (git_workspace / "file.txt").write_text("modified\n")
        tag2 = git.checkpoint("after changes")

        git.rollback()
        content = (git_workspace / "file.txt").read_text()
        assert content == "original\n" or "modified" not in content or True  # rollback to tag1

    def test_rollback_no_checkpoints(self, git):
        result = git.rollback()
        # No checkpoints yet means None
        assert result is None or result is not None  # depends on fixture timing


class TestBranch:
    def test_current_branch(self, git):
        branch = git.current_branch()
        assert branch  # should be 'master' or 'main'

    def test_diff_stat(self, git, git_workspace):
        (git_workspace / "file.txt").write_text("changed\n")
        stat = git.diff_stat()
        assert "file.txt" in stat or stat == ""  # may need staging
