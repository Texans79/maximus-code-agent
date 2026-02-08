"""GitTool — ToolBase adapter for GitOps."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult, _param
from mca.tools.git_ops import GitOps


class GitTool(ToolBase):
    def __init__(self, git: GitOps) -> None:
        self._git = git

    @property
    def name(self) -> str:
        return "git"

    @property
    def description(self) -> str:
        return "Git checkpoint, rollback, branch, diff, log operations"

    def actions(self) -> dict[str, str]:
        return {
            "git_checkpoint": "Create a checkpoint commit + tag",
            "git_rollback": "Rollback to the most recent MCA checkpoint",
            "git_branch": "Create and switch to a new branch",
            "git_diff": "Show current uncommitted changes summary",
            "git_log": "Show recent commit log (oneline)",
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "git_checkpoint",
                "description": "Create a checkpoint commit + tag for safe rollback",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": _param("string", "Optional checkpoint message"),
                    },
                    "required": [],
                },
            }},
            {"type": "function", "function": {
                "name": "git_rollback",
                "description": "Rollback to the most recent MCA checkpoint",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "git_branch",
                "description": "Create and switch to a new branch",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": _param("string", "New branch name"),
                    },
                    "required": ["name"],
                },
            }},
            {"type": "function", "function": {
                "name": "git_diff",
                "description": "Show current uncommitted changes summary",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "git_log",
                "description": "Show recent commit log (oneline format)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "n": _param("integer", "Number of commits to show (default: 10)"),
                    },
                    "required": [],
                },
            }},
        ]

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "git_checkpoint":
            tag = self._git.checkpoint(args.get("message"))
            return ToolResult(ok=True, data={"tag": tag})
        if action == "git_rollback":
            ref = self._git.rollback()
            return ToolResult(ok=ref is not None, data={"ref": ref or ""})
        if action == "git_branch":
            name = self._git.create_branch(args["name"])
            return ToolResult(ok=True, data={"branch": name})
        if action == "git_diff":
            stat = self._git.diff_stat()
            return ToolResult(ok=True, data={"diff_stat": stat})
        if action == "git_log":
            lines = self._git.log_oneline(args.get("n", 10))
            return ToolResult(ok=True, data={"log": lines})
        raise ValueError(f"Unknown git action: {action}")

    def verify(self) -> ToolResult:
        is_repo = self._git.is_repo()
        return ToolResult(ok=is_repo, data={"tool": "git", "is_repo": is_repo})
