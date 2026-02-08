"""DoneTool — signals task completion."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult, _param


class DoneTool(ToolBase):
    @property
    def name(self) -> str:
        return "done"

    @property
    def description(self) -> str:
        return "Signal task completion with a summary"

    def actions(self) -> dict[str, str]:
        return {"done": "Signal that the task is finished"}

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {
            "name": "done",
            "description": "Signal that the task is finished. Provide a summary of what was accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": _param("string", "Summary of what was accomplished"),
                },
                "required": ["summary"],
            },
        }}]

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(ok=True, data={
            "done": True, "summary": args.get("summary", "")})
