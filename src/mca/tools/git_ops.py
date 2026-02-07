"""GitOps — checkpoint commits, branch management, and rollback."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from mca.log import get_logger

log = get_logger("git_ops")

MCA_TAG_PREFIX = "mca-checkpoint-"


class GitOps:
    """Git operations scoped to a workspace directory."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", "-C", str(self.workspace)] + list(args)
        log.debug("git %s", " ".join(args))
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def is_repo(self) -> bool:
        r = self._run("rev-parse", "--is-inside-work-tree", check=False)
        return r.returncode == 0

    def ensure_repo(self) -> None:
        if not self.is_repo():
            self._run("init")
            self._run("add", "-A")
            self._run("commit", "-m", "Initial commit", "--allow-empty")
            log.info("initialized git repo at %s", self.workspace)

    def current_branch(self) -> str:
        r = self._run("branch", "--show-current", check=False)
        return r.stdout.strip() or "HEAD"

    def has_changes(self) -> bool:
        r = self._run("status", "--porcelain", check=False)
        return bool(r.stdout.strip())

    def checkpoint(self, message: str | None = None) -> str:
        """Create a checkpoint commit + tag. Returns the tag name."""
        import time as _time
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        usec = int(_time.time() * 1000) % 1000
        tag = f"{MCA_TAG_PREFIX}{ts}-{usec:03d}"
        msg = message or f"MCA checkpoint {ts}"

        if self.has_changes():
            self._run("add", "-A")
            self._run("commit", "-m", msg)
        else:
            # Create an empty checkpoint commit for the tag
            self._run("commit", "--allow-empty", "-m", msg)

        self._run("tag", tag)
        log.info("checkpoint: %s", tag)
        return tag

    def rollback(self) -> str | None:
        """Rollback to the most recent MCA checkpoint tag."""
        r = self._run("tag", "-l", f"{MCA_TAG_PREFIX}*", "--sort=-creatordate", check=False)
        tags = r.stdout.strip().splitlines()
        if not tags:
            log.warning("no MCA checkpoints found")
            return None

        # The latest checkpoint — rollback to the commit BEFORE it
        # (the checkpoint was created AT the start of the task)
        latest = tags[0].strip()

        # If there's a second tag, roll back to that (before current task)
        if len(tags) > 1:
            target = tags[1].strip()
        else:
            target = latest

        log.info("rolling back to %s", target)
        self._run("reset", "--hard", target, check=False)

        # Remove the latest tag
        self._run("tag", "-d", latest, check=False)

        return target

    def create_branch(self, name: str) -> str:
        """Create and switch to a new branch."""
        self._run("checkout", "-b", name, check=False)
        log.info("created branch %s", name)
        return name

    def diff_stat(self) -> str:
        """Return a summary of current changes."""
        r = self._run("diff", "--stat", "HEAD", check=False)
        return r.stdout.strip()

    def log_oneline(self, n: int = 10) -> list[str]:
        r = self._run("log", f"-{n}", "--oneline", check=False)
        return r.stdout.strip().splitlines()
