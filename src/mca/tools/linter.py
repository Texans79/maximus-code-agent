"""LinterFormatter — detect and run linters/formatters, return structured issues."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mca.log import get_logger
from mca.tools.base import ToolBase, ToolResult, _param
from mca.tools.safe_shell import SafeShell

log = get_logger("linter")

LINTERS = [
    {
        "name": "ruff",
        "detect": lambda ws: any(ws.glob("**/*.py")),
        "check": "ruff --version",
        "lint": "ruff check --output-format=json .",
        "fix": "ruff check --fix .",
        "format": "ruff format .",
    },
    {
        "name": "eslint",
        "detect": lambda ws: (ws / "package.json").exists() and any(ws.glob("**/*.{js,ts}")),
        "check": "npx eslint --version",
        "lint": "npx eslint --format=json .",
        "fix": "npx eslint --fix .",
        "format": None,
    },
    {
        "name": "prettier",
        "detect": lambda ws: (ws / "package.json").exists(),
        "check": "npx prettier --version",
        "lint": "npx prettier --check .",
        "fix": None,
        "format": "npx prettier --write .",
    },
]


class LinterFormatter(ToolBase):
    """Detect and run linters/formatters."""

    def __init__(self, shell: SafeShell, workspace: Path) -> None:
        self._shell = shell
        self._ws = workspace

    @property
    def name(self) -> str:
        return "linter"

    @property
    def description(self) -> str:
        return "Detect and run linters (ruff, eslint) and formatters (ruff format, prettier)"

    def actions(self) -> dict[str, str]:
        return {
            "lint": "Run detected linters, return issues",
            "format_code": "Run detected formatters to auto-fix style",
            "detect_linters": "List which linters are available for this project",
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "lint",
                "description": "Run detected linters and return issues found",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "linter": _param("string", "Specific linter to run (default: all detected)"),
                    },
                    "required": [],
                },
            }},
            {"type": "function", "function": {
                "name": "format_code",
                "description": "Run detected formatters to auto-fix code style",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
            {"type": "function", "function": {
                "name": "detect_linters",
                "description": "List which linters/formatters are available for this project",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }},
        ]

    def _detect_available(self) -> list[dict]:
        available = []
        for linter in LINTERS:
            try:
                if not linter["detect"](self._ws):
                    continue
                r = self._shell.run(linter["check"])
                if r.exit_code == 0:
                    available.append(linter)
            except Exception:
                continue
        return available

    def _parse_ruff_json(self, stdout: str) -> list[dict]:
        try:
            issues = json.loads(stdout)
            return [
                {
                    "file": i.get("filename", ""),
                    "line": i.get("location", {}).get("row", 0),
                    "col": i.get("location", {}).get("column", 0),
                    "severity": "warning" if i.get("fix") else "error",
                    "code": i.get("code", ""),
                    "message": i.get("message", ""),
                }
                for i in issues
            ]
        except (json.JSONDecodeError, TypeError):
            return []

    def _parse_eslint_json(self, stdout: str) -> list[dict]:
        try:
            data = json.loads(stdout)
            issues = []
            for report in data:
                for msg in report.get("messages", []):
                    issues.append({
                        "file": report.get("filePath", ""),
                        "line": msg.get("line", 0),
                        "col": msg.get("column", 0),
                        "severity": "error" if msg.get("severity") == 2 else "warning",
                        "code": msg.get("ruleId", ""),
                        "message": msg.get("message", ""),
                    })
            return issues
        except (json.JSONDecodeError, TypeError):
            return []

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "detect_linters":
            available = self._detect_available()
            return ToolResult(ok=True, data={"linters": [l["name"] for l in available]})

        if action == "lint":
            target = args.get("linter")
            available = self._detect_available()
            if target:
                available = [l for l in available if l["name"] == target]

            all_issues: list[dict] = []
            for linter in available:
                r = self._shell.run(linter["lint"])
                if linter["name"] == "ruff":
                    all_issues.extend(self._parse_ruff_json(r.stdout))
                elif linter["name"] == "eslint":
                    all_issues.extend(self._parse_eslint_json(r.stdout))
                elif r.exit_code != 0:
                    all_issues.append({
                        "file": "", "line": 0, "col": 0,
                        "severity": "error", "code": linter["name"],
                        "message": (r.stdout + r.stderr)[:1000],
                    })

            errors = sum(1 for i in all_issues if i["severity"] == "error")
            return ToolResult(
                ok=errors == 0,
                data={"issues": all_issues[:100], "total": len(all_issues)},
            )

        if action == "format_code":
            available = self._detect_available()
            formatted = []
            for linter in available:
                cmd = linter.get("format") or linter.get("fix")
                if cmd:
                    r = self._shell.run(cmd)
                    formatted.append({
                        "formatter": linter["name"],
                        "exit_code": r.exit_code,
                        "output": r.stdout[:500],
                    })
            return ToolResult(ok=True, data={"formatted_by": formatted})

        raise ValueError(f"Unknown linter action: {action}")

    def verify(self) -> ToolResult:
        available = self._detect_available()
        return ToolResult(ok=True, data={
            "tool": "linter", "available": [l["name"] for l in available]})
