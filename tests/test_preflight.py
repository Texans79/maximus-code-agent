"""Tests for PreflightRunner — environment validation checks."""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mca.preflight.checks import PreflightRunner, PreflightReport, CheckResult


class TestCheckResult:
    def test_ok_check(self):
        c = CheckResult(ok=True, name="Test", detail="All good")
        assert c.ok
        assert not c.warn

    def test_warning_check(self):
        c = CheckResult(ok=True, name="Test", detail="Low disk", warn=True)
        assert c.ok
        assert c.warn

    def test_failed_check(self):
        c = CheckResult(ok=False, name="Test", detail="DB down")
        assert not c.ok


class TestPreflightReport:
    def test_empty_report(self):
        r = PreflightReport()
        assert r.passed == 0
        assert r.warned == 0
        assert r.failed == 0
        assert r.ready

    def test_all_pass(self):
        r = PreflightReport(checks=[
            CheckResult(ok=True, name="A"),
            CheckResult(ok=True, name="B"),
        ])
        assert r.passed == 2
        assert r.warned == 0
        assert r.failed == 0
        assert r.ready

    def test_with_warnings(self):
        r = PreflightReport(checks=[
            CheckResult(ok=True, name="A"),
            CheckResult(ok=True, name="B", warn=True),
        ])
        assert r.passed == 1
        assert r.warned == 1
        assert r.failed == 0
        assert r.ready  # Warnings don't block

    def test_with_failure(self):
        r = PreflightReport(checks=[
            CheckResult(ok=True, name="A"),
            CheckResult(ok=False, name="B", detail="failed"),
        ])
        assert r.passed == 1
        assert r.failed == 1
        assert not r.ready

    def test_to_journal_detail(self):
        r = PreflightReport(checks=[
            CheckResult(ok=True, name="A", detail="ok"),
            CheckResult(ok=False, name="B", detail="bad"),
        ])
        d = r.to_journal_detail()
        assert d["passed"] == 1
        assert d["failed"] == 1
        assert d["ready"] is False
        assert len(d["checks"]) == 2


class TestPreflightDatabase:
    def test_database_connected(self):
        store = MagicMock()
        store.conn.execute.return_value = None
        runner = PreflightRunner(MagicMock(), Path("/tmp"), store=store)
        result = runner._check_database()
        assert result.ok
        assert "Connected" in result.detail

    def test_database_no_store(self):
        runner = PreflightRunner(MagicMock(), Path("/tmp"), store=None)
        result = runner._check_database()
        assert not result.ok

    def test_database_error(self):
        store = MagicMock()
        store.conn.execute.side_effect = Exception("Connection refused")
        runner = PreflightRunner(MagicMock(), Path("/tmp"), store=store)
        result = runner._check_database()
        assert not result.ok
        assert "Connection" in result.detail


class TestPreflightDisk:
    def test_disk_space_ok(self, tmp_path):
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_disk_space()
        assert result.ok
        assert "free" in result.detail


class TestPreflightWorkspace:
    def test_workspace_exists_and_writable(self, tmp_path):
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_workspace()
        assert result.ok

    def test_workspace_not_found(self):
        runner = PreflightRunner(MagicMock(), Path("/nonexistent/ws"))
        result = runner._check_workspace()
        assert not result.ok
        assert "Not found" in result.detail


class TestPreflightGitRepo:
    def test_git_repo_clean(self, tmp_path):
        # Create a git repo
        import subprocess
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
                       capture_output=True)
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_git_repo()
        assert result.ok
        assert "Clean" in result.detail

    def test_git_repo_dirty(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
                       capture_output=True)
        (tmp_path / "dirty.txt").write_text("changes")
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_git_repo()
        assert result.ok
        assert result.warn
        assert "Dirty" in result.detail

    def test_not_git_repo(self, tmp_path):
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_git_repo()
        assert result.ok
        assert result.warn


class TestPreflightLLM:
    @patch("urllib.request.urlopen")
    def test_llm_reachable(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        from mca.config import Config
        cfg = Config({"llm": {"base_url": "http://localhost:8000/v1"}})
        runner = PreflightRunner(cfg, Path("/tmp"))
        result = runner._check_llm_endpoint()
        assert result.ok
        assert "Reachable" in result.detail

    @patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
    def test_llm_unreachable(self, mock_urlopen):
        from mca.config import Config
        cfg = Config({"llm": {"base_url": "http://localhost:8000/v1"}})
        runner = PreflightRunner(cfg, Path("/tmp"))
        result = runner._check_llm_endpoint()
        assert not result.ok


class TestPreflightTools:
    def test_tools_all_ok(self):
        registry = MagicMock()
        registry.verify_all.return_value = {
            "fs": MagicMock(ok=True),
            "shell": MagicMock(ok=True),
        }
        runner = PreflightRunner(MagicMock(), Path("/tmp"), registry=registry)
        result = runner._check_tools()
        assert result.ok
        assert "2 tools OK" in result.detail

    def test_tools_with_failure(self):
        registry = MagicMock()
        registry.verify_all.return_value = {
            "fs": MagicMock(ok=True),
            "shell": MagicMock(ok=False),
        }
        runner = PreflightRunner(MagicMock(), Path("/tmp"), registry=registry)
        result = runner._check_tools()
        assert result.ok  # Tools failure is a warning, not a fail
        assert result.warn

    def test_tools_no_registry(self):
        runner = PreflightRunner(MagicMock(), Path("/tmp"), registry=None)
        result = runner._check_tools()
        assert result.ok
        assert result.warn


class TestPreflightTempFiles:
    def test_no_tmp_dir(self, tmp_path):
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_temp_files()
        assert result.ok
        assert "No tmp dir" in result.detail

    def test_few_temp_files(self, tmp_path):
        tmp_dir = tmp_path / ".mca" / "tmp"
        tmp_dir.mkdir(parents=True)
        for i in range(5):
            (tmp_dir / f"file{i}.tmp").write_text("data")
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_temp_files()
        assert result.ok
        assert not result.warn

    def test_many_temp_files(self, tmp_path):
        tmp_dir = tmp_path / ".mca" / "tmp"
        tmp_dir.mkdir(parents=True)
        for i in range(101):
            (tmp_dir / f"file{i}.tmp").write_text("data")
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_temp_files()
        assert result.ok
        assert result.warn


class TestPreflightLogRotation:
    def test_no_log_file(self, tmp_path):
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_log_rotation()
        assert result.ok

    def test_small_log_file(self, tmp_path):
        log_dir = tmp_path / ".mca"
        log_dir.mkdir(parents=True)
        (log_dir / "mca.jsonl").write_text("small log\n")
        runner = PreflightRunner(MagicMock(), tmp_path)
        result = runner._check_log_rotation()
        assert result.ok
        assert not result.warn


class TestPreflightRam:
    def test_ram_check(self):
        runner = PreflightRunner(MagicMock(), Path("/tmp"))
        result = runner._check_ram()
        assert result.ok
        assert "available" in result.detail


class TestPreflightRunAll:
    def test_run_all_returns_report(self, tmp_path):
        from mca.config import Config
        cfg = Config({"llm": {"base_url": "http://localhost:99999/v1"}})
        runner = PreflightRunner(cfg, tmp_path, store=None, registry=None)
        report = runner.run_all()
        assert isinstance(report, PreflightReport)
        assert len(report.checks) == 10

    def test_run_all_handles_check_crash(self, tmp_path):
        from mca.config import Config
        cfg = Config({"llm": {"base_url": "http://localhost:8000/v1"}})
        runner = PreflightRunner(cfg, tmp_path)
        # Monkey-patch a check to crash
        runner._check_database = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        report = runner.run_all()
        # Should still complete and include the crashed check
        assert any(not c.ok and "crashed" in c.detail.lower() for c in report.checks)


class TestPreflightPrintReport:
    def test_print_report_no_crash(self, tmp_path, capsys):
        report = PreflightReport(checks=[
            CheckResult(ok=True, name="A", detail="ok"),
            CheckResult(ok=True, name="B", detail="warn", warn=True),
            CheckResult(ok=False, name="C", detail="fail"),
        ])
        runner = PreflightRunner(MagicMock(), tmp_path)
        runner.print_report(report)
