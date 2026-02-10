"""Tests for orchestrator approval, tool dispatch, and done validation."""
import json
from unittest.mock import MagicMock, patch

import pytest

from mca.orchestrator.approval import ApprovalDenied, ApprovalMode, approve_plan
from mca.orchestrator.loop import (
    _execute_tool, _build_system_prompt, _validate_done, _build_context,
    _detect_failure_pattern, _summarize_tool_history, _detect_stuck,
    _needs_auto_read, MAX_ITERATIONS,
)
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


class TestDetectFailurePattern:
    def test_no_failures(self):
        assert _detect_failure_pattern([]) is None

    def test_no_pattern_below_threshold(self):
        failures = [
            {"failure_reason": "Error A happened"},
            {"failure_reason": "Error B happened"},
        ]
        assert _detect_failure_pattern(failures) is None

    def test_detects_repeated_pattern(self):
        failures = [
            {"failure_reason": "Max iterations reached without completion"},
            {"failure_reason": "Max iterations reached without completion"},
            {"failure_reason": "Max iterations reached without completion"},
        ]
        pattern = _detect_failure_pattern(failures)
        assert pattern is not None
        assert "Max iterations" in pattern

    def test_groups_by_first_50_chars(self):
        failures = [
            {"failure_reason": "Connection refused: database server not responding on port 5432"},
            {"failure_reason": "Connection refused: database server not responding on port 5433"},
            {"failure_reason": "Connection refused: database server not responding on port 5434"},
        ]
        pattern = _detect_failure_pattern(failures)
        assert pattern is not None
        assert "Connection refused" in pattern

    def test_ignores_none_reasons(self):
        failures = [
            {"failure_reason": None},
            {"failure_reason": None},
            {"failure_reason": None},
        ]
        assert _detect_failure_pattern(failures) is None

    def test_custom_min_count(self):
        failures = [
            {"failure_reason": "Error X"},
            {"failure_reason": "Error X"},
        ]
        assert _detect_failure_pattern(failures, min_count=2) is not None
        assert _detect_failure_pattern(failures, min_count=3) is None


class TestSummarizeToolHistory:
    def test_empty_history(self):
        result = _summarize_tool_history([])
        assert "no tool calls" in result.lower()

    def test_single_ok_entry(self):
        history = [{"tool": "read_file", "args": {"path": "main.py"}, "result": {"ok": True}}]
        result = _summarize_tool_history(history)
        assert "read_file" in result
        assert "OK" in result

    def test_failed_entry_shows_error(self):
        history = [{"tool": "run_command", "args": {"command": "ls"}, "result": {"ok": False, "error": "Permission denied"}}]
        result = _summarize_tool_history(history)
        assert "FAIL" in result
        assert "Permission denied" in result

    def test_truncates_long_history(self):
        history = [{"tool": f"tool_{i}", "args": {}, "result": {"ok": True}} for i in range(30)]
        result = _summarize_tool_history(history, max_entries=5)
        lines = [l for l in result.strip().split("\n") if l.strip()]
        assert len(lines) == 5


class TestDetectStuck:
    def test_not_stuck_with_few_entries(self):
        history = [{"tool": "read_file", "args": {"path": "a.py"}, "result": {"ok": True}}]
        assert _detect_stuck(history) is None

    def test_not_stuck_with_different_tools(self):
        history = [
            {"tool": "read_file", "args": {"path": "a.py"}, "result": {"ok": True}},
            {"tool": "search", "args": {"pattern": "foo"}, "result": {"ok": True}},
            {"tool": "list_files", "args": {}, "result": {"ok": True}},
        ]
        assert _detect_stuck(history) is None

    def test_stuck_same_tool_same_args(self):
        history = [
            {"tool": "replace_in_file", "args": {"path": "a.py", "old_text": "x"}, "result": {"ok": False}},
            {"tool": "replace_in_file", "args": {"path": "a.py", "old_text": "x"}, "result": {"ok": False}},
            {"tool": "replace_in_file", "args": {"path": "a.py", "old_text": "x"}, "result": {"ok": False}},
        ]
        result = _detect_stuck(history)
        assert result is not None
        assert result[0] == "replace_in_file"
        assert result[1] == 3

    def test_not_stuck_same_tool_different_args(self):
        history = [
            {"tool": "read_file", "args": {"path": "a.py"}, "result": {"ok": True}},
            {"tool": "read_file", "args": {"path": "b.py"}, "result": {"ok": True}},
            {"tool": "read_file", "args": {"path": "c.py"}, "result": {"ok": True}},
        ]
        assert _detect_stuck(history) is None


class TestNeedsAutoRead:
    def test_non_edit_tool_no_read(self):
        assert _needs_auto_read("read_file", {"path": "a.py"}, []) is None
        assert _needs_auto_read("list_files", {}, []) is None
        assert _needs_auto_read("run_tests", {}, []) is None

    def test_edit_without_prior_read(self):
        result = _needs_auto_read("replace_in_file", {"path": "main.py"}, [])
        assert result == "main.py"

    def test_edit_with_prior_read(self):
        history = [{"tool": "read_file", "args": {"path": "main.py"}, "result": {"ok": True}}]
        result = _needs_auto_read("replace_in_file", {"path": "main.py"}, history)
        assert result is None

    def test_edit_file_also_checked(self):
        result = _needs_auto_read("edit_file", {"path": "foo.py"}, [])
        assert result == "foo.py"

    def test_different_file_still_needs_read(self):
        history = [{"tool": "read_file", "args": {"path": "other.py"}, "result": {"ok": True}}]
        result = _needs_auto_read("replace_in_file", {"path": "main.py"}, history)
        assert result == "main.py"

    def test_no_path_in_args(self):
        assert _needs_auto_read("edit_file", {}, []) is None


class TestMaxIterationsChanged:
    def test_max_iterations_is_25(self):
        assert MAX_ITERATIONS == 25


class TestBuildSystemPromptDelegation:
    """Verify the _build_system_prompt wrapper still works for existing callers."""

    def test_basic_prompt(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        prompt = _build_system_prompt(registry)
        assert "Maximus Code Agent" in prompt
        assert "THINKING DISCIPLINE" in prompt

    def test_with_workspace_name(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        prompt = _build_system_prompt(registry, workspace_name="test-project")
        assert "test-project" in prompt

    def test_spike_mode(self, tmp_path):
        from mca.config import Config
        from mca.tools.registry import build_registry
        cfg = Config({
            "shell": {"denylist": [], "allowlist": [], "timeout": 30},
            "git": {"auto_checkpoint": False, "branch_prefix": "mca/"},
        })
        registry = build_registry(tmp_path, cfg)
        prompt = _build_system_prompt(registry, spike_mode=True)
        assert "SPIKE MODE" in prompt
