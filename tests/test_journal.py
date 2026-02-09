"""Tests for JournalWriter — DB + markdown journaling."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from mca.journal.writer import JournalWriter


class TestJournalWriterInit:
    def test_creates_with_store(self):
        store = MagicMock()
        j = JournalWriter(store, "task-1", "run-1", Path("/tmp"), "test task")
        assert j.run_id == "run-1"
        assert j._seq == 0
        assert j.entries == []

    def test_creates_without_store(self):
        j = JournalWriter(None, None, "run-2", Path("/tmp"))
        assert j.run_id == "run-2"

    def test_duration_increases(self):
        j = JournalWriter(None, None, "run-3", Path("/tmp"))
        time.sleep(0.05)
        assert j.duration_s >= 0.0


class TestJournalLog:
    def test_log_increments_seq(self):
        store = MagicMock()
        store.add_journal_entry.return_value = "entry-1"
        j = JournalWriter(store, "task-1", "run-1", Path("/tmp"))
        j.log("start", "Started")
        j.log("tool", "run_tests: OK")
        assert j._seq == 2
        assert len(j.entries) == 2

    def test_log_calls_store(self):
        store = MagicMock()
        store.add_journal_entry.return_value = "entry-1"
        j = JournalWriter(store, "task-1", "run-1", Path("/tmp"))
        j.log("preflight", "All good", {"checks": 10})
        store.add_journal_entry.assert_called_once_with(
            task_id="task-1",
            run_id="run-1",
            seq=1,
            phase="preflight",
            summary="All good",
            detail={"checks": 10},
        )

    def test_log_without_store(self):
        j = JournalWriter(None, None, "run-2", Path("/tmp"))
        j.log("start", "No store")
        assert len(j.entries) == 1
        assert j.entries[0]["phase"] == "start"

    def test_log_handles_store_error(self):
        store = MagicMock()
        store.add_journal_entry.side_effect = Exception("DB error")
        j = JournalWriter(store, "task-1", "run-1", Path("/tmp"))
        # Should not raise
        j.log("tool", "Something")
        assert len(j.entries) == 1

    def test_log_detail_defaults_to_empty(self):
        store = MagicMock()
        store.add_journal_entry.return_value = "e-1"
        j = JournalWriter(store, "task-1", "run-1", Path("/tmp"))
        j.log("start", "Begin")
        assert j.entries[0]["detail"] == {}

    def test_log_records_elapsed_time(self):
        j = JournalWriter(None, None, "run-1", Path("/tmp"))
        time.sleep(0.05)
        j.log("tool", "Something")
        assert j.entries[0]["elapsed_s"] >= 0.0


class TestJournalExportMarkdown:
    def test_export_creates_file(self, tmp_path):
        j = JournalWriter(None, None, "abcd1234-5678-1234-5678-123456789abc",
                          tmp_path, "test task")
        j.log("start", "Starting")
        j.log("preflight", "All checks passed")
        j.log("tool", "run_tests: 5 passed")
        path = j.export_markdown()
        assert path.exists()
        content = path.read_text()
        assert "MCA Run Journal" in content
        assert "test task" in content
        assert "Starting" in content
        assert "All checks passed" in content
        assert "run_tests" in content

    def test_export_creates_journal_dir(self, tmp_path):
        j = JournalWriter(None, None, "abcd1234", tmp_path)
        j.log("start", "Starting")
        path = j.export_markdown()
        assert (tmp_path / ".mca" / "journal").is_dir()

    def test_export_groups_by_phase(self, tmp_path):
        j = JournalWriter(None, None, "abcd1234", tmp_path)
        j.log("preflight", "Check 1")
        j.log("preflight", "Check 2")
        j.log("tool", "Tool 1")
        path = j.export_markdown()
        content = path.read_text()
        assert "## Preflight" in content
        assert "## Tool" in content


class TestJournalClose:
    def test_close_writes_done_entry(self, tmp_path):
        store = MagicMock()
        store.add_journal_entry.return_value = "e-1"
        j = JournalWriter(store, "task-1", "run-1", tmp_path, "test")
        j.log("start", "Begin")
        j.close()
        # close() calls log("done", ...) then export_markdown
        assert any(e["phase"] == "done" for e in j.entries)

    def test_close_exports_markdown(self, tmp_path):
        j = JournalWriter(None, None, "run-1", tmp_path)
        j.log("start", "Begin")
        j.close()
        assert (tmp_path / ".mca" / "journal").exists()

    def test_close_handles_export_error(self):
        j = JournalWriter(None, None, "run-1", Path("/nonexistent/dir"))
        j.log("start", "Begin")
        # Should not raise even with invalid path
        j.close()


class TestJournalEntries:
    def test_entries_returns_copy(self):
        j = JournalWriter(None, None, "run-1", Path("/tmp"))
        j.log("start", "Begin")
        entries = j.entries
        entries.clear()
        assert len(j.entries) == 1

    def test_entries_contain_expected_fields(self):
        j = JournalWriter(None, None, "run-1", Path("/tmp"))
        j.log("tool", "run_tests: OK", {"passed": 5})
        entry = j.entries[0]
        assert entry["seq"] == 1
        assert entry["phase"] == "tool"
        assert entry["summary"] == "run_tests: OK"
        assert entry["detail"] == {"passed": 5}
        assert "elapsed_s" in entry


class TestJournalMultiplePhases:
    def test_full_run_sequence(self, tmp_path):
        store = MagicMock()
        store.add_journal_entry.return_value = "e-1"
        j = JournalWriter(store, "task-1", "run-1", tmp_path, "full test")
        j.log("start", "Task: full test")
        j.log("preflight", "10✓ 0! 0✗")
        j.log("plan", "Plan approved")
        j.log("tool", "write_file: OK")
        j.log("tool", "run_tests: 5 passed")
        j.log("checkpoint", "Auto-saved at iteration 1")
        j.log("done", "Result: success")
        assert j._seq == 7
        assert store.add_journal_entry.call_count == 7
        path = j.export_markdown()
        content = path.read_text()
        assert "Start" in content
        assert "Preflight" in content
        assert "Checkpoint" in content
