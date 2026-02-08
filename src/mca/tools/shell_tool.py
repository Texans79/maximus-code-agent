"""ShellTool — ToolBase adapter for SafeShell."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult
from mca.tools.safe_shell import SafeShell
from mca.utils.secrets import redact


class ShellTool(ToolBase):
    def __init__(self, shell: SafeShell) -> None:
        self._shell = shell

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return "Execute shell commands in workspace with safety denylist"

    def actions(self) -> dict[str, str]:
        return {"run_command": "Execute a shell command in the workspace"}

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action != "run_command":
            raise ValueError(f"Unknown shell action: {action}")
        cmd = args.get("cmd") or args.get("command", "")
        result = self._shell.run(cmd)
        return ToolResult(
            ok=result.exit_code == 0,
            data={
                "exit_code": result.exit_code,
                "stdout": redact(result.stdout[:5000]),
                "stderr": redact(result.stderr[:2000]),
                "duration_s": result.duration_s,
            },
        )

    def verify(self) -> ToolResult:
        result = self._shell.run("echo mca-verify-ok")
        return ToolResult(
            ok=result.exit_code == 0 and "mca-verify-ok" in result.stdout,
            data={"tool": "shell"},
        )
