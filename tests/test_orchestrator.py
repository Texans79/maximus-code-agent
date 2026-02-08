"""Tests for orchestrator approval, tool parsing, and registry-based dispatch."""
import json
from unittest.mock import MagicMock, patch

import pytest

from mca.orchestrator.approval import ApprovalDenied, ApprovalMode, approve_plan
from mca.orchestrator.loop import _execute_tool, _parse_tool_calls, _build_system_prompt


class TestApprovalMode:
    def test_auto_approves(self):
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


class TestRegistryDispatch:
    @pytest.fixture
    def workspace(self, tmp_path):
        (tmp_path / "test.txt").write_text("hello world\n")
        return tmp_path

    @pytest.fixture
    def registry(self, workspace):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": ["rm -rf /"], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        return build_registry(workspace, cfg)

    def test_read_file(self, registry):
        result = _execute_tool("read_file", {"path": "test.txt"}, registry, "auto")
        assert result["ok"]
        assert "hello" in result.get("data", result).get("content", "")

    def test_write_file(self, registry, workspace):
        result = _execute_tool("write_file", {"path": "new.py", "content": "x = 1\n"},
                               registry, "auto")
        assert result["ok"]
        assert (workspace / "new.py").read_text() == "x = 1\n"

    def test_list_files(self, registry):
        result = _execute_tool("list_files", {}, registry, "auto")
        assert result["ok"]

    def test_search(self, registry):
        result = _execute_tool("search", {"pattern": "hello"}, registry, "auto")
        assert result["ok"]

    def test_run_command(self, registry):
        result = _execute_tool("run_command", {"cmd": "echo test"}, registry, "auto")
        assert result["ok"]

    def test_run_denied_command(self, registry):
        result = _execute_tool("run_command", {"cmd": "rm -rf /"}, registry, "auto")
        assert not result["ok"]

    def test_done(self, registry):
        result = _execute_tool("done", {"summary": "all good"}, registry, "auto")
        assert result["ok"]
        assert result.get("data", result).get("done") or result.get("done")

    def test_unknown_tool(self, registry):
        result = _execute_tool("nope", {}, registry, "auto")
        assert not result["ok"]


class TestBuildSystemPrompt:
    def test_includes_actions(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        prompt = _build_system_prompt(registry)
        assert "read_file" in prompt
        assert "write_file" in prompt
        assert "run_command" in prompt
        assert "done" in prompt
        assert "Maximus Code Agent" in prompt
