"""ToolBase — abstract interface for all MCA tools."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Uniform result returned by every tool invocation."""
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"ok": self.ok, **self.data}
        if self.error:
            d["error"] = self.error
        return d


class ToolBase(ABC):
    """Abstract base class for all MCA tools.

    Each tool exposes named actions. The orchestrator calls
    execute(action, args) and gets back a ToolResult.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short unique name, e.g. 'fs', 'shell', 'git'."""

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description for LLM prompt and CLI listing."""

    @abstractmethod
    def actions(self) -> dict[str, str]:
        """Return {action_name: description} for supported actions."""

    @abstractmethod
    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        """Execute an action. Raise ValueError for unknown actions."""

    def verify(self) -> ToolResult:
        """Self-test. Override for real checks."""
        return ToolResult(ok=True, data={"tool": self.name, "status": "available"})
