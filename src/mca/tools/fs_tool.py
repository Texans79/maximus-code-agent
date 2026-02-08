"""FSTool — ToolBase adapter for SafeFS."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult, _param
from mca.tools.safe_fs import SafeFS


class FSTool(ToolBase):
    """Filesystem operations jailed to workspace."""

    def __init__(self, fs: SafeFS) -> None:
        self._fs = fs

    @property
    def name(self) -> str:
        return "filesystem"

    @property
    def description(self) -> str:
        return "Workspace-jailed file read/write/search/list operations"

    def actions(self) -> dict[str, str]:
        return {
            "read_file": "Read a file (relative to workspace)",
            "write_file": "Create or overwrite a file",
            "replace_in_file": "Replace exact text in a file (search-and-replace)",
            "edit_file": "Apply a unified diff patch to an existing file",
            "search": "Grep for a regex pattern in workspace files",
            "list_files": "List workspace file tree",
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "read_file",
                "description": "Read a file from the workspace",
                "parameters": {
                    "type": "object",
                    "properties": {"path": _param("string", "Relative path to file")},
                    "required": ["path"],
                },
            }},
            {"type": "function", "function": {
                "name": "write_file",
                "description": "Create or overwrite a file in the workspace",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": _param("string", "Relative path to file"),
                        "content": _param("string", "Full file content to write"),
                    },
                    "required": ["path", "content"],
                },
            }},
            {"type": "function", "function": {
                "name": "replace_in_file",
                "description": "Replace exact text in an existing file. Use this instead of edit_file for reliable edits. Provide the exact existing text and the replacement.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": _param("string", "Relative path to file"),
                        "old_text": _param("string", "Exact text to find in the file (must match exactly)"),
                        "new_text": _param("string", "Replacement text"),
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            }},
            {"type": "function", "function": {
                "name": "edit_file",
                "description": "Apply a unified diff patch. Prefer replace_in_file for simple edits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": _param("string", "Relative path to file"),
                        "diff": _param("string", "Unified diff text"),
                    },
                    "required": ["path", "diff"],
                },
            }},
            {"type": "function", "function": {
                "name": "search",
                "description": "Grep for a regex pattern across workspace files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": _param("string", "Regex pattern to search for"),
                        "glob": _param("string", "File glob filter (default: **/*)")
                    },
                    "required": ["pattern"],
                },
            }},
            {"type": "function", "function": {
                "name": "list_files",
                "description": "List the workspace file tree",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "depth": _param("integer", "Max directory depth (default: 3)"),
                    },
                    "required": [],
                },
            }},
        ]

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "read_file":
            content = self._fs.read(args["path"])
            if len(content) > 8000:
                content = content[:8000] + "\n... [truncated]"
            return ToolResult(ok=True, data={"content": content})

        if action == "write_file":
            self._fs.write_force(args["path"], args["content"])
            return ToolResult(ok=True, data={"wrote": args["path"],
                                              "bytes": len(args["content"])})

        if action == "replace_in_file":
            ok = self._fs.replace_in_file(args["path"], args["old_text"], args["new_text"])
            if ok:
                return ToolResult(ok=True, data={"replaced": args["path"]})
            return ToolResult(ok=False, error=f"old_text not found in {args['path']}")

        if action == "edit_file":
            ok = self._fs.apply_diff(args["path"], args["diff"])
            return ToolResult(ok=ok, data={"edited": args["path"]})

        if action == "search":
            results = self._fs.search(args["pattern"], args.get("glob", "**/*"))
            return ToolResult(ok=True, data={"matches": results[:50],
                                              "truncated": len(results) > 50})

        if action == "list_files":
            tree = self._fs.tree(max_depth=args.get("depth", 3))
            return ToolResult(ok=True, data={"files": tree[:200]})

        raise ValueError(f"Unknown fs action: {action}")

    def verify(self) -> ToolResult:
        try:
            tree = self._fs.tree(max_depth=1)
            return ToolResult(ok=True, data={"tool": "filesystem", "files": len(tree)})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))
