"""Tests for orchestrator approval, tool dispatch, and done validation."""
import json
from unittest.mock import MagicMock, patch

import pytest

from mca.orchestrator.approval import ApprovalDenied, ApprovalMode, approve_plan
from mca.orchestrator.loop import _execute_tool, _build_system_prompt, _validate_done, _build_context
from mca.llm.client import ToolCall


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

    def _tc(self, name: str, args: dict) -> ToolCall:
        """Helper to create a ToolCall."""
        return ToolCall(id=f"test-{name}", name=name, arguments=args)

    def test_read_file(self, registry):
        tc = self._tc("read_file", {"path": "test.txt"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]
        assert "hello" in result.get("content", "")

    def test_write_file(self, registry, workspace):
        tc = self._tc("write_file", {"path": "new.py", "content": "x = 1\n"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]
        assert (workspace / "new.py").read_text() == "x = 1\n"

    def test_replace_in_file(self, registry, workspace):
        tc = self._tc("replace_in_file", {"path": "test.txt", "old_text": "hello", "new_text": "goodbye"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]
        assert "goodbye" in (workspace / "test.txt").read_text()

    def test_list_files(self, registry):
        tc = self._tc("list_files", {})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]

    def test_search(self, registry):
        tc = self._tc("search", {"pattern": "hello"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]

    def test_run_command(self, registry):
        tc = self._tc("run_command", {"command": "echo test"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]

    def test_run_denied_command(self, registry):
        tc = self._tc("run_command", {"command": "rm -rf /"})
        result = _execute_tool(tc, registry, "auto")
        assert not result["ok"]

    def test_done(self, registry):
        tc = self._tc("done", {"summary": "all good"})
        result = _execute_tool(tc, registry, "auto")
        assert result["ok"]
        assert result.get("done") is True

    def test_unknown_tool(self, registry):
        tc = self._tc("nope", {})
        result = _execute_tool(tc, registry, "auto")
        assert not result["ok"]


class TestValidateDone:
    def _tc_done(self, summary: str = "finished") -> ToolCall:
        return ToolCall(id="done-1", name="done", arguments={"summary": summary})

    def test_no_tests_run(self):
        """done() should be rejected if no tests were run."""
        err = _validate_done(self._tc_done(), [])
        assert err is not None
        assert "run tests" in err.lower()

    def test_tests_failed(self):
        """done() should be rejected if the most recent tests failed."""
        history = [
            {"tool": "run_tests", "result": {"ok": False, "failed": 2, "output": "FAILED test_a"}},
        ]
        err = _validate_done(self._tc_done(), history)
        assert err is not None
        assert "failing" in err.lower()

    def test_tests_passed(self):
        """done() should be accepted if the most recent tests passed."""
        history = [
            {"tool": "run_tests", "result": {"ok": True, "passed": 5, "failed": 0}},
        ]
        err = _validate_done(self._tc_done(), history)
        assert err is None

    def test_old_fail_then_pass(self):
        """done() should look at most recent test, not earlier ones."""
        history = [
            {"tool": "run_tests", "result": {"ok": False, "failed": 1}},
            {"tool": "replace_in_file", "result": {"ok": True}},
            {"tool": "run_tests", "result": {"ok": True, "passed": 5, "failed": 0}},
        ]
        err = _validate_done(self._tc_done(), history)
        assert err is None

    def test_pass_then_fail(self):
        """done() should reject if last test run failed even if earlier ones passed."""
        history = [
            {"tool": "run_tests", "result": {"ok": True, "passed": 5}},
            {"tool": "replace_in_file", "result": {"ok": True}},
            {"tool": "run_tests", "result": {"ok": False, "failed": 2}},
        ]
        err = _validate_done(self._tc_done(), history)
        assert err is not None


class TestBuildSystemPrompt:
    def test_static_prompt_content(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        prompt = _build_system_prompt(registry)
        assert "Maximus Code Agent" in prompt
        assert "replace_in_file" in prompt
        assert "run_tests" in prompt
        assert "done" in prompt


class TestToolDefinitions:
    def test_registry_aggregates_definitions(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        defs = registry.tool_definitions()
        names = [d["function"]["name"] for d in defs]
        # Core actions present
        assert "read_file" in names
        assert "write_file" in names
        assert "replace_in_file" in names
        assert "run_command" in names
        assert "done" in names
        assert "run_tests" in names
        assert "git_checkpoint" in names
        # All have proper structure
        for d in defs:
            assert d["type"] == "function"
            assert "name" in d["function"]
            assert "parameters" in d["function"]
            assert d["function"]["parameters"]["type"] == "object"

    def test_definitions_have_descriptions(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        defs = registry.tool_definitions()
        for d in defs:
            assert d["function"].get("description"), f"{d['function']['name']} has no description"


class TestBuildContext:
    def test_build_context_with_files(self, tmp_path):
        (tmp_path / "foo.py").write_text("x = 1")
        (tmp_path / "bar.py").write_text("y = 2")
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        ctx = _build_context(registry)
        assert "foo.py" in ctx
        assert "bar.py" in ctx
