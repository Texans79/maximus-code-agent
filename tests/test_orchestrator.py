"""Tests for orchestrator approval and tool execution."""
import json
from unittest.mock import MagicMock, patch

import pytest

from mca.orchestrator.approval import ApprovalDenied, ApprovalMode, approve_plan
from mca.orchestrator.loop import _execute_tool, _parse_tool_calls
from mca.tools.safe_fs import SafeFS
from mca.tools.safe_shell import SafeShell


class TestApprovalMode:
    def test_auto_approves(self):
        # Auto mode should not raise
        result = approve_plan("test plan", ApprovalMode.AUTO)
        assert result is True

    @patch("mca.orchestrator.approval.console")
    def test_ask_mode_approve(self, mock_console):
        mock_console.input.return_value = "y"
        result = approve_plan("test plan", ApprovalMode.ASK)
        assert result is True

    @patch("mca.orchestrator.approval.console")
    def test_ask_mode_deny(self, mock_console):
        mock_console.input.return_value = "n"
        with pytest.raises(ApprovalDenied):
            approve_plan("test plan", ApprovalMode.ASK)


class TestParseToolCalls:
    def test_valid_json(self):
        text = '[{"tool": "read_file", "args": {"path": "test.py"}}]'
        calls = _parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["tool"] == "read_file"

    def test_json_embedded_in_text(self):
        text = 'Here is my plan:\n[{"tool": "done", "args": {"summary": "ok"}}]\nDone.'
        calls = _parse_tool_calls(text)
        assert calls[0]["tool"] == "done"

    def test_invalid_json_becomes_done(self):
        text = "I can't do that."
        calls = _parse_tool_calls(text)
        assert calls[0]["tool"] == "done"
        assert "can't" in calls[0]["args"]["summary"]


class TestToolExecution:
    @pytest.fixture
    def workspace(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello world\n")
        return tmp_path

    @pytest.fixture
    def fs(self, workspace):
        return SafeFS(workspace)

    @pytest.fixture
    def shell(self, workspace):
        return SafeShell(workspace)

    def test_read_file(self, fs, shell):
        result = _execute_tool("read_file", {"path": "test.txt"}, fs, shell, "auto")
        assert result["ok"]
        assert "hello" in result["content"]

    def test_write_file(self, fs, shell, workspace):
        result = _execute_tool("write_file", {"path": "new.py", "content": "x = 1\n"}, fs, shell, "auto")
        assert result["ok"]
        assert (workspace / "new.py").read_text() == "x = 1\n"

    def test_list_files(self, fs, shell):
        result = _execute_tool("list_files", {}, fs, shell, "auto")
        assert result["ok"]
        assert any("test.txt" in f for f in result["files"])

    def test_search(self, fs, shell):
        result = _execute_tool("search", {"pattern": "hello"}, fs, shell, "auto")
        assert result["ok"]
        assert len(result["matches"]) == 1

    def test_run_command(self, fs, shell):
        result = _execute_tool("run_command", {"cmd": "echo test"}, fs, shell, "auto")
        assert result["ok"]
        assert "test" in result["stdout"]

    def test_run_denied_command(self, fs, shell):
        result = _execute_tool("run_command", {"cmd": "rm -rf /"}, fs, shell, "auto")
        assert not result["ok"]
        assert "Blocked" in result["error"]

    def test_done(self, fs, shell):
        result = _execute_tool("done", {"summary": "all good"}, fs, shell, "auto")
        assert result["ok"]
        assert result["done"]

    def test_unknown_tool(self, fs, shell):
        result = _execute_tool("nope", {}, fs, shell, "auto")
        assert not result["ok"]
