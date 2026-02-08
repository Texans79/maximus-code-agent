"""TestRunner — detect framework, run tests, return structured results."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mca.log import get_logger
from mca.tools.base import ToolBase, ToolResult
from mca.tools.safe_shell import SafeShell

log = get_logger("test_runner")

# (framework, marker_files, command)
DETECTORS: list[tuple[str, list[str], str]] = [
    ("pytest", ["conftest.py", "pytest.ini", "pyproject.toml"],
     "python -m pytest --tb=short -q"),
    ("jest", ["jest.config.js", "jest.config.ts", "jest.config.mjs"],
     "npx jest --no-coverage"),
    ("go_test", ["go.mod"], "go test ./..."),
    ("cargo_test", ["Cargo.toml"], "cargo test"),
]


class TestRunner(ToolBase):
    """Detect and run test frameworks, parse results."""

    def __init__(self, shell: SafeShell, workspace: Path) -> None:
        self._shell = shell
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "test_runner"

    @property
    def description(self) -> str:
        return "Detect test framework, run tests, return structured pass/fail results"

    def actions(self) -> dict[str, str]:
        return {
            "run_tests": "Run the project test suite (auto-detects framework)",
            "detect_test_framework": "Detect which test framework the project uses",
        }

    def detect_framework(self) -> tuple[str, str] | None:
        """Return (framework_name, run_command) or None."""
        for framework, markers, cmd in DETECTORS:
            for marker in markers:
                path = self._workspace / marker
                if not path.exists():
                    continue
                if framework == "pytest" and marker == "pyproject.toml":
                    if "pytest" not in path.read_text(errors="ignore"):
                        continue
                if framework == "jest" and marker.startswith("jest.config"):
                    pass  # config file existence is sufficient
                return framework, cmd
        # Fallback: tests/ directory → pytest
        if (self._workspace / "tests").is_dir():
            return "pytest", "python -m pytest --tb=short -q"
        return None

    def _parse_pytest(self, stdout: str, stderr: str) -> dict:
        combined = stdout + stderr
        m = re.search(
            r"(\d+) passed"
            r"(?:.*?(\d+) failed)?"
            r"(?:.*?(\d+) error)?"
            r"(?:.*?(\d+) skipped)?"
            r".*?in ([\d.]+)s",
            combined,
        )
        if m:
            return {
                "passed": int(m.group(1)),
                "failed": int(m.group(2) or 0),
                "errors": int(m.group(3) or 0),
                "skipped": int(m.group(4) or 0),
                "duration_s": float(m.group(5)),
            }
        # Fallback: look for "X failed" alone
        fail_m = re.search(r"(\d+) failed", combined)
        pass_m = re.search(r"(\d+) passed", combined)
        return {
            "passed": int(pass_m.group(1)) if pass_m else 0,
            "failed": int(fail_m.group(1)) if fail_m else 0,
            "errors": 0, "skipped": 0, "duration_s": 0.0,
        }

    def _parse_jest(self, stdout: str) -> dict:
        m = re.search(
            r"Tests:\s+(?:(\d+) failed,?\s*)?(?:(\d+) skipped,?\s*)?(\d+) passed",
            stdout,
        )
        if m:
            return {
                "passed": int(m.group(3)),
                "failed": int(m.group(1) or 0),
                "errors": 0,
                "skipped": int(m.group(2) or 0),
                "duration_s": 0.0,
            }
        return {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "duration_s": 0.0}

    def _parse_go(self, stdout: str) -> dict:
        return {
            "passed": stdout.count("--- PASS"),
            "failed": stdout.count("--- FAIL"),
            "errors": 0, "skipped": 0, "duration_s": 0.0,
        }

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "detect_test_framework":
            detected = self.detect_framework()
            if detected:
                return ToolResult(ok=True, data={"framework": detected[0], "command": detected[1]})
            return ToolResult(ok=False, error="No test framework detected")

        if action == "run_tests":
            detected = self.detect_framework()
            if not detected:
                return ToolResult(ok=False, error="No test framework detected")

            framework, cmd = detected
            cmd = args.get("command", cmd)
            path = args.get("path", "")
            if path:
                cmd = f"{cmd} {path}"

            result = self._shell.run(cmd)

            parsers = {
                "pytest": lambda: self._parse_pytest(result.stdout, result.stderr),
                "jest": lambda: self._parse_jest(result.stdout),
                "go_test": lambda: self._parse_go(result.stdout),
                "cargo_test": lambda: self._parse_go(result.stdout),
            }
            summary = parsers.get(framework, lambda: {})()

            return ToolResult(
                ok=result.exit_code == 0,
                data={
                    "framework": framework,
                    **summary,
                    "exit_code": result.exit_code,
                    "output": (result.stdout + result.stderr)[:5000],
                },
            )

        raise ValueError(f"Unknown test_runner action: {action}")

    def verify(self) -> ToolResult:
        detected = self.detect_framework()
        fw = detected[0] if detected else "none"
        return ToolResult(ok=True, data={"tool": "test_runner", "framework": fw})
