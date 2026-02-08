"""DepDoctor — check Python venvs, Node modules, Go modules; diagnose issues."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mca.log import get_logger
from mca.tools.base import ToolBase, ToolResult
from mca.tools.safe_shell import SafeShell

log = get_logger("dep_doctor")


class DepDoctor(ToolBase):
    """Check environment health: venvs, installed deps, diagnosis."""

    def __init__(self, shell: SafeShell, workspace: Path) -> None:
        self._shell = shell
        self._ws = workspace

    @property
    def name(self) -> str:
        return "dep_doctor"

    @property
    def description(self) -> str:
        return "Check Python venv, Node modules, Go modules; verify deps installed"

    def actions(self) -> dict[str, str]:
        return {
            "check_environment": "Full environment health check",
            "check_python": "Check Python venv and installed packages",
            "check_node": "Check Node.js and installed modules",
            "check_go": "Check Go modules",
        }

    def _check_python(self) -> dict[str, Any]:
        result: dict[str, Any] = {"detected": False}
        has_pyproject = (self._ws / "pyproject.toml").exists()
        has_reqs = (self._ws / "requirements.txt").exists()
        result["detected"] = has_pyproject or has_reqs
        if not result["detected"]:
            return result

        # Venv
        for vd in (".venv", "venv"):
            p = self._ws / vd
            if p.is_dir() and (p / "bin" / "python").exists():
                result["venv_path"] = str(vd)
                break
        result["venv_found"] = "venv_path" in result

        # Python version
        r = self._shell.run("python --version")
        result["python_version"] = r.stdout.strip() if r.exit_code == 0 else "not found"

        # pip check
        r = self._shell.run("pip check")
        result["pip_check_ok"] = r.exit_code == 0
        if r.exit_code != 0:
            result["pip_issues"] = r.stdout[:500]

        return result

    def _check_node(self) -> dict[str, Any]:
        result: dict[str, Any] = {"detected": False}
        if not (self._ws / "package.json").exists():
            return result
        result["detected"] = True
        result["node_modules"] = (self._ws / "node_modules").is_dir()

        r = self._shell.run("node --version")
        result["node_version"] = r.stdout.strip() if r.exit_code == 0 else "not found"

        if result["node_modules"]:
            r = self._shell.run("npm ls --depth=0 2>&1 | tail -5")
            result["npm_ok"] = r.exit_code == 0
        return result

    def _check_go(self) -> dict[str, Any]:
        result: dict[str, Any] = {"detected": False}
        if not (self._ws / "go.mod").exists():
            return result
        result["detected"] = True

        r = self._shell.run("go version")
        result["go_version"] = r.stdout.strip() if r.exit_code == 0 else "not found"

        r = self._shell.run("go mod verify")
        result["mod_ok"] = r.exit_code == 0
        return result

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "check_python":
            return ToolResult(ok=True, data=self._check_python())
        if action == "check_node":
            return ToolResult(ok=True, data=self._check_node())
        if action == "check_go":
            return ToolResult(ok=True, data=self._check_go())
        if action == "check_environment":
            data = {
                "python": self._check_python(),
                "node": self._check_node(),
                "go": self._check_go(),
            }
            data["ecosystems"] = [k for k, v in data.items() if v.get("detected")]
            return ToolResult(ok=True, data=data)
        raise ValueError(f"Unknown dep_doctor action: {action}")

    def verify(self) -> ToolResult:
        ecosystems = []
        if (self._ws / "pyproject.toml").exists() or (self._ws / "requirements.txt").exists():
            ecosystems.append("python")
        if (self._ws / "package.json").exists():
            ecosystems.append("node")
        if (self._ws / "go.mod").exists():
            ecosystems.append("go")
        return ToolResult(ok=True, data={"tool": "dep_doctor", "ecosystems": ecosystems})
