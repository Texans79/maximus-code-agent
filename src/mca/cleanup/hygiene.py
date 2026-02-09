"""CleanupRunner — post-run hygiene: orphans, temps, logs, old journals."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from mca.log import get_logger

log = get_logger("cleanup")

_TEMP_MAX_AGE_HOURS = 24
_LOG_MAX_SIZE_MB = 50
_LOG_KEEP_ROTATIONS = 3
_JOURNAL_MAX_DAYS = 30


@dataclass
class CleanupReport:
    orphans_killed: int = 0
    temps_removed: int = 0
    log_rotated: bool = False
    journals_pruned: int = 0
    errors: list[str] = field(default_factory=list)

    def to_journal_detail(self) -> dict[str, Any]:
        return {
            "orphans_killed": self.orphans_killed,
            "temps_removed": self.temps_removed,
            "log_rotated": self.log_rotated,
            "journals_pruned": self.journals_pruned,
            "errors": self.errors,
        }


class CleanupRunner:
    """Post-run cleanup: kill orphans, clean temps, rotate logs, prune journals."""

    def __init__(self, workspace: Path, config=None) -> None:
        self._workspace = workspace
        self._config = config

    def run_all(self) -> CleanupReport:
        report = CleanupReport()
        try:
            report.orphans_killed = self.kill_orphans()
        except Exception as e:
            report.errors.append(f"kill_orphans: {e}")
            log.warning("Cleanup kill_orphans failed: %s", e)
        try:
            report.temps_removed = self.clean_temps()
        except Exception as e:
            report.errors.append(f"clean_temps: {e}")
            log.warning("Cleanup clean_temps failed: %s", e)
        try:
            report.log_rotated = self.rotate_logs()
        except Exception as e:
            report.errors.append(f"rotate_logs: {e}")
            log.warning("Cleanup rotate_logs failed: %s", e)
        try:
            report.journals_pruned = self.prune_old_journals()
        except Exception as e:
            report.errors.append(f"prune_old_journals: {e}")
            log.warning("Cleanup prune_old_journals failed: %s", e)
        return report

    def kill_orphans(self) -> int:
        """Kill stale MCA subprocesses (not self)."""
        my_pid = os.getpid()
        killed = 0
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                if info["pid"] == my_pid:
                    continue
                cmdline = info.get("cmdline") or []
                cmd_str = " ".join(cmdline)
                if "mca" in (info.get("name") or "") or "mca run" in cmd_str:
                    proc.terminate()
                    killed += 1
                    log.info("Terminated orphan MCA process %d", info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return killed

    def clean_temps(self, max_age_hours: int = _TEMP_MAX_AGE_HOURS) -> int:
        """Remove files in .mca/tmp/ older than max_age_hours."""
        tmp_dir = self._workspace / ".mca" / "tmp"
        if not tmp_dir.exists():
            return 0
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        for f in tmp_dir.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError as e:
                log.debug("Failed to remove temp file %s: %s", f, e)
        if removed:
            log.info("Removed %d temp files", removed)
        return removed

    def rotate_logs(self) -> bool:
        """Rotate .mca/mca.jsonl if >50MB. Keeps up to 3 rotations."""
        log_file = self._workspace / ".mca" / "mca.jsonl"
        if not log_file.exists():
            return False
        size_mb = log_file.stat().st_size / (1024 * 1024)
        if size_mb <= _LOG_MAX_SIZE_MB:
            return False

        # Rotate: .3 -> delete, .2 -> .3, .1 -> .2, current -> .1
        for i in range(_LOG_KEEP_ROTATIONS, 0, -1):
            src = log_file.parent / f"mca.jsonl.{i}"
            dst = log_file.parent / f"mca.jsonl.{i + 1}"
            if i == _LOG_KEEP_ROTATIONS and src.exists():
                src.unlink()
            elif src.exists():
                src.rename(dst)
        log_file.rename(log_file.parent / "mca.jsonl.1")
        log.info("Rotated log file (was %.1fMB)", size_mb)
        return True

    def prune_old_journals(self, days: int = _JOURNAL_MAX_DAYS) -> int:
        """Delete journal markdown files older than N days."""
        journal_dir = self._workspace / ".mca" / "journal"
        if not journal_dir.exists():
            return 0
        cutoff = time.time() - (days * 86400)
        pruned = 0
        for f in journal_dir.iterdir():
            try:
                if f.is_file() and f.suffix == ".md" and f.stat().st_mtime < cutoff:
                    f.unlink()
                    pruned += 1
            except OSError as e:
                log.debug("Failed to prune journal %s: %s", f, e)
        if pruned:
            log.info("Pruned %d old journal files", pruned)
        return pruned
