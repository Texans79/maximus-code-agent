"""SafeFS — workspace-jailed file operations with diff-based editing."""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

from mca.log import get_logger

log = get_logger("safe_fs")


class WorkspaceViolation(Exception):
    """Raised when a path escapes the workspace jail."""


class SafeFS:
    """All file I/O is jailed to workspace. No symlink escape, no absolute outside."""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise FileNotFoundError(f"Workspace not found: {self.workspace}")

    # ── Path validation ──────────────────────────────────────────────────

    def _jail(self, rel_path: str | Path) -> Path:
        """Resolve a path and ensure it's inside the workspace. Hard deny escapes."""
        p = Path(rel_path)
        # Reject absolute paths outside workspace
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.workspace / p).resolve()

        # Follow symlinks and verify still inside workspace
        try:
            real = resolved.resolve(strict=False)
        except (OSError, ValueError):
            real = resolved

        if not str(real).startswith(str(self.workspace)):
            raise WorkspaceViolation(
                f"Path escapes workspace: {rel_path!r} -> {real} (workspace={self.workspace})"
            )
        # Extra check for .. traversal in the string form
        norm = os.path.normpath(str(rel_path))
        if norm.startswith("..") or "/../" in str(rel_path):
            raise WorkspaceViolation(f"Path traversal detected: {rel_path!r}")

        return real

    # ── Read operations ──────────────────────────────────────────────────

    def read(self, rel_path: str) -> str:
        """Read a file inside workspace."""
        target = self._jail(rel_path)
        log.debug("read %s", target)
        return target.read_text(encoding="utf-8", errors="replace")

    def exists(self, rel_path: str) -> bool:
        target = self._jail(rel_path)
        return target.exists()

    def list_dir(self, rel_path: str = ".") -> list[str]:
        target = self._jail(rel_path)
        if not target.is_dir():
            return []
        return sorted(str(p.relative_to(self.workspace)) for p in target.iterdir())

    def tree(self, max_depth: int = 3) -> list[str]:
        """Return a flat list of relative paths, respecting depth."""
        out: list[str] = []
        for root, dirs, files in os.walk(self.workspace):
            depth = str(root).replace(str(self.workspace), "").count(os.sep)
            if depth >= max_depth:
                dirs.clear()
                continue
            # Skip hidden / common junk
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".") and d not in {
                "node_modules", "__pycache__", ".git", "venv", ".venv", ".tox",
            }]
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), self.workspace)
                out.append(rel)
        return out

    def search(self, pattern: str, glob: str = "**/*") -> list[dict]:
        """Grep-like search inside workspace files."""
        results: list[dict] = []
        regex = re.compile(pattern, re.IGNORECASE)
        for path in self.workspace.glob(glob):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.workspace))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    results.append({"file": rel, "line": i, "text": line.rstrip()})
        return results

    # ── Write operations (diff-based) ────────────────────────────────────

    def write(self, rel_path: str, content: str) -> Path:
        """Write a NEW file (must not already exist, or use apply_diff for edits)."""
        target = self._jail(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        log.info("wrote %s (%d bytes)", rel_path, len(content))
        return target

    def write_force(self, rel_path: str, content: str) -> Path:
        """Full rewrite — only for cases explicitly flagged as needed."""
        target = self._jail(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        log.info("wrote (force) %s (%d bytes)", rel_path, len(content))
        return target

    def generate_diff(self, rel_path: str, new_content: str) -> str:
        """Generate a unified diff between existing file and proposed content."""
        target = self._jail(rel_path)
        if target.exists():
            old_lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        else:
            old_lines = []
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
        )
        return "".join(diff)

    def apply_diff(self, rel_path: str, unified_diff: str) -> bool:
        """Apply a unified diff (patch) to a file inside the workspace."""
        target = self._jail(rel_path)
        if not target.exists():
            # New file from diff
            return self._apply_patch_subprocess(target, unified_diff)

        return self._apply_patch_subprocess(target, unified_diff)

    def _apply_patch_subprocess(self, target: Path, diff_text: str) -> bool:
        """Apply patch using the patch command or manual fallback."""
        # Try subprocess patch first
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
                f.write(diff_text)
                patch_file = f.name

            result = subprocess.run(
                ["patch", "--no-backup-if-mismatch", "-p1", "-d", str(self.workspace)],
                input=diff_text,
                capture_output=True,
                text=True,
                timeout=10,
            )
            os.unlink(patch_file)
            if result.returncode == 0:
                log.info("patch applied to %s", target)
                return True
            log.warning("patch failed (rc=%d): %s", result.returncode, result.stderr[:200])
        except FileNotFoundError:
            log.debug("patch command not found, using manual apply")
        except Exception as e:
            log.warning("patch subprocess error: %s", e)

        # Manual fallback: parse unified diff and apply
        return self._manual_patch(target, diff_text)

    def _manual_patch(self, target: Path, diff_text: str) -> bool:
        """Minimal manual unified-diff applier."""
        if target.exists():
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            lines = []

        hunks = _parse_unified_diff(diff_text)
        if not hunks:
            log.warning("no hunks parsed from diff")
            return False

        offset = 0
        for hunk in hunks:
            start = hunk["old_start"] - 1 + offset  # 0-indexed
            # Remove old lines, insert new
            end = start + hunk["old_count"]
            lines[start:end] = hunk["new_lines"]
            offset += len(hunk["new_lines"]) - hunk["old_count"]

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        log.info("manual patch applied to %s", target)
        return True

    def replace_in_file(self, rel_path: str, old_text: str, new_text: str) -> bool:
        """Replace exact text in a file. More reliable than diff/patch.

        Returns True if replacement was made, False if old_text not found.
        Only replaces the first occurrence.
        """
        target = self._jail(rel_path)
        if not target.exists():
            log.warning("replace_in_file: file not found: %s", rel_path)
            return False
        content = target.read_text(encoding="utf-8", errors="replace")
        if old_text not in content:
            log.warning("replace_in_file: old_text not found in %s", rel_path)
            return False
        new_content = content.replace(old_text, new_text, 1)
        target.write_text(new_content, encoding="utf-8")
        log.info("replaced text in %s (%d→%d chars)", rel_path, len(old_text), len(new_text))
        return True

    def mkdir(self, rel_path: str) -> Path:
        target = self._jail(rel_path)
        target.mkdir(parents=True, exist_ok=True)
        return target


def _parse_unified_diff(diff_text: str) -> list[dict]:
    """Parse hunks from a unified diff string."""
    hunks: list[dict] = []
    current: dict | None = None
    hunk_re = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

    for line in diff_text.splitlines():
        m = hunk_re.match(line)
        if m:
            if current:
                hunks.append(current)
            current = {
                "old_start": int(m.group(1)),
                "old_count": int(m.group(2) or 1),
                "new_start": int(m.group(3)),
                "new_count": int(m.group(4) or 1),
                "new_lines": [],
            }
            continue
        if current is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            current["new_lines"].append(line[1:])
        elif line.startswith(" "):
            current["new_lines"].append(line[1:])
        # Lines starting with - are removed (not added to new_lines)

    if current:
        hunks.append(current)
    return hunks
