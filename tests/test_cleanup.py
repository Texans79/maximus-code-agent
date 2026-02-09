"""Tests for CleanupRunner — post-run hygiene."""
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mca.cleanup.hygiene import CleanupRunner, CleanupReport


class TestCleanupReport:
    def test_defaults(self):
        r = CleanupReport()
        assert r.orphans_killed == 0
        assert r.temps_removed == 0
        assert not r.log_rotated
        assert r.journals_pruned == 0
        assert r.errors == []

    def test_to_journal_detail(self):
        r = CleanupReport(orphans_killed=1, temps_removed=5,
                          log_rotated=True, journals_pruned=3)
        d = r.to_journal_detail()
        assert d["orphans_killed"] == 1
        assert d["temps_removed"] == 5
        assert d["log_rotated"] is True
        assert d["journals_pruned"] == 3


class TestCleanTemps:
    def test_no_tmp_dir(self, tmp_path):
        runner = CleanupRunner(tmp_path)
        assert runner.clean_temps() == 0

    def test_removes_old_files(self, tmp_path):
        tmp_dir = tmp_path / ".mca" / "tmp"
        tmp_dir.mkdir(parents=True)
        old_file = tmp_dir / "old.tmp"
        old_file.write_text("old data")
        # Make file appear old
        old_time = time.time() - (25 * 3600)
        os.utime(old_file, (old_time, old_time))
        new_file = tmp_dir / "new.tmp"
        new_file.write_text("new data")
        runner = CleanupRunner(tmp_path)
        removed = runner.clean_temps()
        assert removed == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        tmp_dir = tmp_path / ".mca" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "recent.tmp").write_text("data")
        runner = CleanupRunner(tmp_path)
        removed = runner.clean_temps()
        assert removed == 0

    def test_custom_max_age(self, tmp_path):
        tmp_dir = tmp_path / ".mca" / "tmp"
        tmp_dir.mkdir(parents=True)
        f = tmp_dir / "test.tmp"
        f.write_text("data")
        old_time = time.time() - 3600  # 1 hour old
        os.utime(f, (old_time, old_time))
        runner = CleanupRunner(tmp_path)
        # With 0.5 hour max age, file should be removed
        removed = runner.clean_temps(max_age_hours=0)
        assert removed == 1


class TestRotateLogs:
    def test_no_log_file(self, tmp_path):
        runner = CleanupRunner(tmp_path)
        assert not runner.rotate_logs()

    def test_small_log_not_rotated(self, tmp_path):
        log_dir = tmp_path / ".mca"
        log_dir.mkdir(parents=True)
        (log_dir / "mca.jsonl").write_text("small\n")
        runner = CleanupRunner(tmp_path)
        assert not runner.rotate_logs()

    def test_large_log_rotated(self, tmp_path):
        log_dir = tmp_path / ".mca"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "mca.jsonl"
        # Create >50MB file
        log_file.write_bytes(b"x" * (51 * 1024 * 1024))
        runner = CleanupRunner(tmp_path)
        assert runner.rotate_logs()
        assert not log_file.exists()
        assert (log_dir / "mca.jsonl.1").exists()

    def test_rotation_chain(self, tmp_path):
        log_dir = tmp_path / ".mca"
        log_dir.mkdir(parents=True)
        # Create existing rotated files
        (log_dir / "mca.jsonl.1").write_text("old1")
        (log_dir / "mca.jsonl.2").write_text("old2")
        # Create oversized current log
        log_file = log_dir / "mca.jsonl"
        log_file.write_bytes(b"x" * (51 * 1024 * 1024))
        runner = CleanupRunner(tmp_path)
        assert runner.rotate_logs()
        assert (log_dir / "mca.jsonl.1").exists()
        assert (log_dir / "mca.jsonl.2").exists()
        assert (log_dir / "mca.jsonl.3").exists()
        # .1 should be the old current, .2 should be old .1
        assert (log_dir / "mca.jsonl.2").read_text() == "old1"
        assert (log_dir / "mca.jsonl.3").read_text() == "old2"


class TestPruneOldJournals:
    def test_no_journal_dir(self, tmp_path):
        runner = CleanupRunner(tmp_path)
        assert runner.prune_old_journals() == 0

    def test_removes_old_journals(self, tmp_path):
        journal_dir = tmp_path / ".mca" / "journal"
        journal_dir.mkdir(parents=True)
        old_file = journal_dir / "old-run.md"
        old_file.write_text("# Old journal")
        old_time = time.time() - (31 * 86400)
        os.utime(old_file, (old_time, old_time))
        new_file = journal_dir / "new-run.md"
        new_file.write_text("# New journal")
        runner = CleanupRunner(tmp_path)
        pruned = runner.prune_old_journals(days=30)
        assert pruned == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_keeps_recent_journals(self, tmp_path):
        journal_dir = tmp_path / ".mca" / "journal"
        journal_dir.mkdir(parents=True)
        (journal_dir / "recent.md").write_text("# Recent")
        runner = CleanupRunner(tmp_path)
        assert runner.prune_old_journals() == 0

    def test_ignores_non_markdown(self, tmp_path):
        journal_dir = tmp_path / ".mca" / "journal"
        journal_dir.mkdir(parents=True)
        old_file = journal_dir / "data.json"
        old_file.write_text("{}")
        old_time = time.time() - (31 * 86400)
        os.utime(old_file, (old_time, old_time))
        runner = CleanupRunner(tmp_path)
        assert runner.prune_old_journals() == 0


class TestKillOrphans:
    @patch("mca.cleanup.hygiene.psutil")
    def test_no_orphans(self, mock_psutil):
        mock_psutil.process_iter.return_value = []
        runner = CleanupRunner(Path("/tmp"))
        assert runner.kill_orphans() == 0

    @patch("os.getpid", return_value=9999)
    @patch("mca.cleanup.hygiene.psutil")
    def test_skips_self(self, mock_psutil, mock_getpid):
        proc = MagicMock()
        proc.info = {"pid": 9999, "name": "mca", "cmdline": ["mca", "run"]}
        mock_psutil.process_iter.return_value = [proc]
        runner = CleanupRunner(Path("/tmp"))
        assert runner.kill_orphans() == 0


class TestRunAll:
    def test_run_all_returns_report(self, tmp_path):
        runner = CleanupRunner(tmp_path)
        report = runner.run_all()
        assert isinstance(report, CleanupReport)

    def test_run_all_handles_errors(self, tmp_path):
        runner = CleanupRunner(tmp_path)
        runner.kill_orphans = MagicMock(side_effect=RuntimeError("boom"))
        report = runner.run_all()
        assert len(report.errors) == 1
        assert "kill_orphans" in report.errors[0]
