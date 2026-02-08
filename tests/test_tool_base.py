"""Tests for ToolBase, ToolResult, and ToolRegistry."""
from typing import Any

import pytest

from mca.tools.base import ToolBase, ToolResult
from mca.tools.registry import ToolRegistry


class FakeTool(ToolBase):
    @property
    def name(self) -> str:
        return "fake"

    @property
    def description(self) -> str:
        return "A fake tool for testing"

    def actions(self) -> dict[str, str]:
        return {"fake_action": "Does a fake thing", "fake_other": "Does another fake thing"}

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "fake_action":
            return ToolResult(ok=True, data={"result": args.get("input", "default")})
        if action == "fake_other":
            return ToolResult(ok=True, data={"other": True})
        raise ValueError(f"Unknown action: {action}")


class AnotherTool(ToolBase):
    @property
    def name(self) -> str:
        return "another"

    @property
    def description(self) -> str:
        return "Another tool"

    def actions(self) -> dict[str, str]:
        return {"another_action": "Does something else"}

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, data={"ran": True})


class TestToolResult:
    def test_to_dict_success(self):
        r = ToolResult(ok=True, data={"value": 42})
        d = r.to_dict()
        assert d["ok"] is True
        assert d["value"] == 42
        assert "error" not in d

    def test_to_dict_error(self):
        r = ToolResult(ok=False, error="something broke")
        d = r.to_dict()
        assert d["ok"] is False
        assert d["error"] == "something broke"


class TestToolRegistry:
    def test_register_and_dispatch(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        result = reg.dispatch("fake_action", {"input": "hello"})
        assert result.ok
        assert result.data["result"] == "hello"

    def test_dispatch_unknown_action(self):
        reg = ToolRegistry()
        result = reg.dispatch("nonexistent", {})
        assert not result.ok
        assert "Unknown action" in result.error

    def test_action_collision_raises(self):
        reg = ToolRegistry()
        reg.register(FakeTool())

        class Conflicting(ToolBase):
            @property
            def name(self) -> str:
                return "conflict"
            @property
            def description(self) -> str:
                return ""
            def actions(self) -> dict[str, str]:
                return {"fake_action": "Conflicts!"}
            def execute(self, action, args):
                pass

        with pytest.raises(ValueError, match="already registered"):
            reg.register(Conflicting())

    def test_duplicate_tool_name_raises(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        with pytest.raises(ValueError, match="Tool already registered"):
            reg.register(FakeTool())

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        reg.register(AnotherTool())
        tools = reg.list_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert names == {"fake", "another"}

    def test_list_actions(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        reg.register(AnotherTool())
        actions = reg.list_actions()
        assert "fake_action" in actions
        assert "fake_other" in actions
        assert "another_action" in actions

    def test_verify_all(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        reg.register(AnotherTool())
        results = reg.verify_all()
        assert len(results) == 2
        assert all(r.ok for r in results.values())

    def test_get_tool(self):
        reg = ToolRegistry()
        reg.register(FakeTool())
        assert reg.get_tool("fake") is not None
        assert reg.get_tool("nonexistent") is None
