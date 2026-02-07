"""SafeShell — sandboxed command execution with denylist, logging, timeouts."""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from mca.log import get_logger
from mca.utils.secrets import redact

log = get_logger("safe_shell")

# Default denylist patterns (matched against the full command string)
DEFAULT_DENYLIST: list[str] = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf .",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "chmod -R 777",
    "chown -R",
    "curl|bash",
    "curl | bash",
    "curl|sh",
    "curl | sh",
    "wget|bash",
    "wget | bash",
    "wget|sh",
    "wget | sh",
    "| bash",
    "| sh -",
    "> /dev/sd",
    ":(){ :|:& };:",
    "fork bomb",
    "init 0",
    "init 6",
    "systemctl stop",
    "systemctl disable",
    "iptables -F",
    "iptables --flush",
]


class DeniedCommandError(Exception):
    """Raised when a command matches the denylist."""


@dataclass
class ShellResult:
    """Result of a shell command execution."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    truncated: bool = False


class SafeShell:
    """Execute shell commands with safety rails."""

    def __init__(
        self,
        workspace: str | Path,
        denylist: Sequence[str] | None = None,
        allowlist: Sequence[str] | None = None,
        timeout: int = 120,
        max_output: int = 50_000,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.denylist = list(denylist or DEFAULT_DENYLIST)
        self.allowlist = list(allowlist or [])
        self.timeout = timeout
        self.max_output = max_output
        self.history: list[ShellResult] = []

    def _check_denied(self, cmd: str) -> None:
        """Check command against denylist. Raise if matched."""
        cmd_lower = cmd.lower().strip()
        # Allowlist takes priority
        for allowed in self.allowlist:
            if allowed.lower() in cmd_lower:
                return
        for pattern in self.denylist:
            if pattern.lower() in cmd_lower:
                raise DeniedCommandError(
                    f"Command denied by safety denylist: {cmd!r} matched pattern {pattern!r}"
                )

    def run(self, cmd: str, env: dict | None = None) -> ShellResult:
        """Run a command in the workspace directory."""
        self._check_denied(cmd)

        log.info("exec: %s", redact(cmd))
        start = time.monotonic()

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
            duration = time.monotonic() - start

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            truncated = False

            if len(stdout) > self.max_output:
                stdout = stdout[:self.max_output] + "\n… [truncated]"
                truncated = True
            if len(stderr) > self.max_output:
                stderr = stderr[:self.max_output] + "\n… [truncated]"
                truncated = True

            result = ShellResult(
                command=cmd,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_s=round(duration, 2),
                truncated=truncated,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            result = ShellResult(
                command=cmd,
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {self.timeout}s",
                duration_s=round(duration, 2),
            )
            log.error("timeout: %s", cmd)
        except Exception as e:
            duration = time.monotonic() - start
            result = ShellResult(
                command=cmd,
                exit_code=-2,
                stdout="",
                stderr=str(e),
                duration_s=round(duration, 2),
            )
            log.error("error: %s — %s", cmd, e)

        self.history.append(result)
        log.info(
            "exit=%d duration=%.1fs stdout=%d stderr=%d",
            result.exit_code, result.duration_s,
            len(result.stdout), len(result.stderr),
        )
        return result
