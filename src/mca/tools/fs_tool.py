"""FSTool — ToolBase adapter for SafeFS."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult
from mca.tools.safe_fs import SafeFS


class FSTool(ToolBase):
    """Filesystem operations jailed to workspace."""

    def __init__(self, fs: SafeFS) -> None:
        self._fs = fs

    @property
    def name(self) -> str:
        return "fs"

    @property
    def description(self) -> str:
        return "Workspace-jailed file read/write/search/list operations"

    def actions(self) -> dict[str, str]:
        return {
            "read_file": "Read a file (relative to workspace)",
            "write_file": "Create or overwrite a file",
            "edit_file": "Apply a unified diff patch to an existing file",
            "search": "Grep for a regex pattern in workspace files",
            "list_files": "List workspace file tree",
        }

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
            return ToolResult(ok=True, data={"tool": "fs", "files": len(tree)})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))
