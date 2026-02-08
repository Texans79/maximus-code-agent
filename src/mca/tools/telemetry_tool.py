"""TelemetryTool — ToolBase adapter for system telemetry."""
from __future__ import annotations

from typing import Any

from mca.tools.base import ToolBase, ToolResult, _param


class TelemetryTool(ToolBase):
    @property
    def name(self) -> str:
        return "telemetry"

    @property
    def description(self) -> str:
        return "Collect system telemetry: CPU, RAM, disk, GPU, NVMe"

    def actions(self) -> dict[str, str]:
        return {"system_status": "Collect CPU/RAM/disk/GPU/NVMe telemetry data"}

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {
            "name": "system_status",
            "description": "Collect CPU/RAM/disk/GPU/NVMe telemetry data",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }}]

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action != "system_status":
            raise ValueError(f"Unknown telemetry action: {action}")
        from mca.telemetry.collectors import collect_all
        return ToolResult(ok=True, data=collect_all())

    def verify(self) -> ToolResult:
        try:
            from mca.telemetry.collectors import collect_all
            data = collect_all()
            return ToolResult(ok=True, data={"tool": "telemetry", "cpu": data["cpu"]["name"]})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))
