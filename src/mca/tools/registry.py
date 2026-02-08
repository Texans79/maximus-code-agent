"""ToolRegistry — maps action names to tool instances."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mca.log import get_logger
from mca.tools.base import ToolBase, ToolResult

log = get_logger("registry")


class ToolRegistry:
    """Registry mapping action names to tool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolBase] = {}
        self._action_map: dict[str, ToolBase] = {}

    def register(self, tool: ToolBase) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        for action in tool.actions():
            if action in self._action_map:
                existing = self._action_map[action].name
                raise ValueError(f"Action '{action}' already registered by '{existing}'")
            self._action_map[action] = tool
        log.debug("registered tool '%s' (%d actions)", tool.name, len(tool.actions()))

    def dispatch(self, action: str, args: dict[str, Any]) -> ToolResult:
        tool = self._action_map.get(action)
        if tool is None:
            return ToolResult(ok=False, error=f"Unknown action: {action}")
        return tool.execute(action, args)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "actions": t.actions()}
            for t in self._tools.values()
        ]

    def list_actions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for tool in self._tools.values():
            out.update(tool.actions())
        return out

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Aggregate OpenAI-format tool definitions from all registered tools."""
        defs: list[dict[str, Any]] = []
        for tool in self._tools.values():
            defs.extend(tool.tool_definitions())
        return defs

    def verify_all(self) -> dict[str, ToolResult]:
        return {name: tool.verify() for name, tool in self._tools.items()}

    def get_tool(self, name: str) -> ToolBase | None:
        return self._tools.get(name)


def build_registry(
    workspace: str | Path,
    config: Any,
    memory_store: Any | None = None,
) -> ToolRegistry:
    """Construct the standard registry with all tools.

    Called once per run_task(). All tools share the same SafeShell and workspace.
    """
    from mca.tools.dep_doctor import DepDoctor
    from mca.tools.done_tool import DoneTool
    from mca.tools.fs_tool import FSTool
    from mca.tools.git_tool import GitTool
    from mca.tools.linter import LinterFormatter
    from mca.tools.memory_tool import MemoryTool
    from mca.tools.repo_indexer import RepoIndexer
    from mca.tools.shell_tool import ShellTool
    from mca.tools.telemetry_tool import TelemetryTool
    from mca.tools.test_runner import TestRunner
    from mca.tools.safe_fs import SafeFS
    from mca.tools.safe_shell import SafeShell
    from mca.tools.git_ops import GitOps

    ws = Path(workspace).resolve()

    fs = SafeFS(ws)
    shell_cfg = config.shell if hasattr(config, "shell") else None
    shell = SafeShell(
        workspace=ws,
        denylist=shell_cfg.as_dict().get("denylist", []) if shell_cfg else [],
        allowlist=shell_cfg.as_dict().get("allowlist", []) if shell_cfg else [],
        timeout=shell_cfg.timeout if shell_cfg and hasattr(shell_cfg, "timeout") else 120,
    )
    git = GitOps(ws)

    reg = ToolRegistry()

    # Core
    reg.register(FSTool(fs))
    reg.register(ShellTool(shell))
    reg.register(GitTool(git))
    reg.register(DoneTool())
    reg.register(TelemetryTool())

    # Memory
    if memory_store:
        reg.register(MemoryTool(memory_store))

    # Multipliers
    reg.register(TestRunner(shell, ws))
    reg.register(RepoIndexer(ws))
    reg.register(LinterFormatter(shell, ws))
    reg.register(DepDoctor(shell, ws))

    return reg
