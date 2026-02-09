"""PreflightRunner — environment validation before task execution."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from mca.log import console, get_logger

log = get_logger("preflight")


@dataclass
class CheckResult:
    ok: bool
    name: str
    detail: str = ""
    warn: bool = False
    elapsed_ms: float = 0.0


@dataclass
class PreflightReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok and not c.warn)

    @property
    def warned(self) -> int:
        return sum(1 for c in self.checks if c.warn)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.ok)

    @property
    def ready(self) -> bool:
        return self.failed == 0

    def to_journal_detail(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "warned": self.warned,
            "failed": self.failed,
            "ready": self.ready,
            "checks": [
                {"name": c.name, "ok": c.ok, "warn": c.warn, "detail": c.detail}
                for c in self.checks
            ],
        }


class PreflightRunner:
    """Run all preflight checks and produce a readiness report."""

    def __init__(self, config, workspace: Path,
                 registry=None, store=None) -> None:
        self._config = config
        self._workspace = workspace
        self._registry = registry
        self._store = store

    def run_all(self) -> PreflightReport:
        report = PreflightReport()
        checks = [
            self._check_database,
            self._check_disk_space,
            self._check_workspace,
            self._check_git_repo,
            self._check_llm_endpoint,
            self._check_tools,
            self._check_orphan_processes,
            self._check_temp_files,
            self._check_log_rotation,
            self._check_ram,
        ]
        for check_fn in checks:
            try:
                result = check_fn()
                report.checks.append(result)
            except Exception as e:
                report.checks.append(CheckResult(
                    ok=False, name=check_fn.__name__.replace("_check_", ""),
                    detail=f"Check crashed: {e}",
                ))
        return report

    def _timed_check(self, name: str, fn) -> CheckResult:
        """Run a check function and time it."""
        t0 = time.monotonic()
        result = fn()
        result.elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        if not result.name:
            result.name = name
        return result

    def _check_database(self) -> CheckResult:
        t0 = time.monotonic()
        if not self._store or not hasattr(self._store, "conn"):
            return CheckResult(ok=False, name="Database",
                               detail="No database connection")
        try:
            self._store.conn.execute("SELECT 1")
            ms = round((time.monotonic() - t0) * 1000, 1)
            return CheckResult(ok=True, name="Database",
                               detail=f"Connected ({ms}ms)", elapsed_ms=ms)
        except Exception as e:
            return CheckResult(ok=False, name="Database",
                               detail=f"Connection failed: {e}")

    def _check_disk_space(self) -> CheckResult:
        try:
            usage = psutil.disk_usage(str(self._workspace))
            free_gb = round(usage.free / (1024 ** 3), 1)
            pct = round(usage.percent, 1)
            detail = f"{free_gb}GB free ({100 - pct:.0f}%)"
            if free_gb < 2:
                return CheckResult(ok=False, name="Disk space", detail=detail)
            if free_gb < 10:
                return CheckResult(ok=True, name="Disk space",
                                   detail=detail, warn=True)
            return CheckResult(ok=True, name="Disk space", detail=detail)
        except Exception as e:
            return CheckResult(ok=False, name="Disk space", detail=str(e))

    def _check_workspace(self) -> CheckResult:
        ws = self._workspace
        if not ws.exists():
            return CheckResult(ok=False, name="Workspace",
                               detail=f"Not found: {ws}")
        if not os.access(ws, os.W_OK):
            return CheckResult(ok=False, name="Workspace",
                               detail=f"Not writable: {ws}")
        return CheckResult(ok=True, name="Workspace",
                           detail=str(ws))

    def _check_git_repo(self) -> CheckResult:
        try:
            from mca.tools.git_ops import GitOps
            git = GitOps(self._workspace)
            if not git.is_repo():
                return CheckResult(ok=True, name="Git repo",
                                   detail="Not a git repo", warn=True)
            if git.has_changes():
                return CheckResult(ok=True, name="Git repo",
                                   detail="Dirty working tree", warn=True)
            return CheckResult(ok=True, name="Git repo",
                               detail="Clean")
        except Exception as e:
            return CheckResult(ok=True, name="Git repo",
                               detail=f"Check skipped: {e}", warn=True)

    def _check_llm_endpoint(self) -> CheckResult:
        t0 = time.monotonic()
        try:
            import urllib.request
            import urllib.error
            base_url = self._config.llm.base_url
            url = f"{base_url}/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                ms = round((time.monotonic() - t0) * 1000, 1)
                return CheckResult(ok=True, name="LLM endpoint",
                                   detail=f"Reachable ({ms}ms)", elapsed_ms=ms)
        except Exception as e:
            return CheckResult(ok=False, name="LLM endpoint",
                               detail=f"Unreachable: {e}")

    def _check_tools(self) -> CheckResult:
        if not self._registry:
            return CheckResult(ok=True, name="Tools",
                               detail="No registry (skipped)", warn=True)
        try:
            results = self._registry.verify_all()
            failed = [n for n, r in results.items() if not r.ok]
            total = len(results)
            if failed:
                return CheckResult(
                    ok=True, name="Tools",
                    detail=f"{len(failed)}/{total} failed: {', '.join(failed)}",
                    warn=True,
                )
            return CheckResult(ok=True, name="Tools",
                               detail=f"All {total} tools OK")
        except Exception as e:
            return CheckResult(ok=True, name="Tools",
                               detail=f"Verify failed: {e}", warn=True)

    def _check_orphan_processes(self) -> CheckResult:
        try:
            my_pid = os.getpid()
            orphans = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    info = proc.info
                    if info["pid"] == my_pid:
                        continue
                    cmdline = info.get("cmdline") or []
                    cmd_str = " ".join(cmdline)
                    if "mca" in (info.get("name") or "") or "mca run" in cmd_str:
                        orphans.append(info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if orphans:
                return CheckResult(
                    ok=True, name="Orphan processes",
                    detail=f"{len(orphans)} stale MCA process(es): {orphans}",
                    warn=True,
                )
            return CheckResult(ok=True, name="Orphan processes",
                               detail="None found")
        except Exception as e:
            return CheckResult(ok=True, name="Orphan processes",
                               detail=f"Check failed: {e}", warn=True)

    def _check_temp_files(self) -> CheckResult:
        tmp_dir = self._workspace / ".mca" / "tmp"
        if not tmp_dir.exists():
            return CheckResult(ok=True, name="Temp files", detail="No tmp dir")
        count = sum(1 for _ in tmp_dir.iterdir())
        if count > 100:
            return CheckResult(ok=True, name="Temp files",
                               detail=f"{count} files (consider cleanup)",
                               warn=True)
        return CheckResult(ok=True, name="Temp files",
                           detail=f"{count} files")

    def _check_log_rotation(self) -> CheckResult:
        log_file = self._workspace / ".mca" / "mca.jsonl"
        if not log_file.exists():
            return CheckResult(ok=True, name="Log rotation",
                               detail="No log file yet")
        size_mb = round(log_file.stat().st_size / (1024 * 1024), 1)
        if size_mb > 50:
            return CheckResult(ok=True, name="Log rotation",
                               detail=f"{size_mb}MB (rotation needed)",
                               warn=True)
        return CheckResult(ok=True, name="Log rotation",
                           detail=f"{size_mb}MB")

    def _check_ram(self) -> CheckResult:
        mem = psutil.virtual_memory()
        avail_gb = round(mem.available / (1024 ** 3), 1)
        if avail_gb < 4:
            return CheckResult(ok=True, name="RAM",
                               detail=f"{avail_gb}GB available (low)",
                               warn=True)
        return CheckResult(ok=True, name="RAM",
                           detail=f"{avail_gb}GB available")

    def print_report(self, report: PreflightReport) -> None:
        """Print a rich-formatted preflight report to console."""
        console.print("\n[bold cyan]── Preflight Report ──[/bold cyan]")
        for c in report.checks:
            if not c.ok:
                marker = "[red][✗][/red]"
            elif c.warn:
                marker = "[yellow][!][/yellow]"
            else:
                marker = "[green][✓][/green]"
            console.print(f"  {marker} {c.name}: {c.detail}")

        status = "[green]READY[/green]" if report.ready else "[red]NOT READY[/red]"
        console.print(
            f"\n  {report.passed} passed, {report.warned} warnings, "
            f"{report.failed} failures — {status}\n"
        )
