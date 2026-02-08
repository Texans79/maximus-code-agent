"""MemoryTool — ToolBase adapter for the memory store."""
from __future__ import annotations

from typing import Any

from mca.memory.base import MemoryStore
from mca.tools.base import ToolBase, ToolResult, _param


class MemoryTool(ToolBase):
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Store and search long-term knowledge entries"

    def actions(self) -> dict[str, str]:
        return {
            "memory_add": "Store a knowledge entry",
            "memory_search": "Search stored knowledge by text query",
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "memory_add",
                "description": "Store a knowledge entry in long-term memory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": _param("string", "Text content to store"),
                        "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
                        "category": _param("string", "Category (default: general)"),
                    },
                    "required": ["content"],
                },
            }},
            {"type": "function", "function": {
                "name": "memory_search",
                "description": "Search stored knowledge by text query",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": _param("string", "Search query text"),
                        "limit": _param("integer", "Max results to return (default: 5)"),
                    },
                    "required": ["query"],
                },
            }},
        ]

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "memory_add":
            mid = self._store.add(
                content=args["content"],
                tags=args.get("tags", []),
                category=args.get("category", "general"),
            )
            return ToolResult(ok=True, data={"id": mid})
        if action == "memory_search":
            results = self._store.search(
                query=args["query"], limit=args.get("limit", 5))
            return ToolResult(ok=True, data={"results": results})
        raise ValueError(f"Unknown memory action: {action}")

    def verify(self) -> ToolResult:
        return ToolResult(ok=True, data={
            "tool": "memory",
            "backend": self._store.backend_name,
            "fallback": self._store.is_fallback,
        })
