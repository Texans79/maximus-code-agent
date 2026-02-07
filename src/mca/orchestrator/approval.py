"""Approval system for auto / ask / paranoid modes."""
from __future__ import annotations

from enum import Enum
from typing import Callable

from rich.panel import Panel
from rich.syntax import Syntax

from mca.log import console


class ApprovalMode(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    PARANOID = "paranoid"


class ApprovalDenied(Exception):
    """User denied the action."""


def _prompt_user(prompt: str) -> bool:
    """Ask the user yes/no."""
    console.print(f"[bold yellow]{prompt}[/bold yellow]")
    while True:
        answer = console.input("[bold]  [y/n]: [/bold]").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        console.print("[dim]  Please enter y or n.[/dim]")


def approve_plan(plan: str, mode: str | ApprovalMode) -> bool:
    """Show a task plan and request approval."""
    mode = ApprovalMode(mode)
    if mode == ApprovalMode.AUTO:
        console.print(Panel(plan, title="Plan (auto-approved)", border_style="green"))
        return True

    console.print(Panel(plan, title="Proposed Plan", border_style="yellow"))
    if not _prompt_user("Approve this plan?"):
        raise ApprovalDenied("Plan rejected by user")
    return True


def approve_diff(filepath: str, diff: str, mode: str | ApprovalMode) -> bool:
    """Show a diff and request approval for file modification."""
    mode = ApprovalMode(mode)
    if mode == ApprovalMode.AUTO:
        return True

    console.print(f"\n[bold]File: {filepath}[/bold]")
    console.print(Syntax(diff, "diff", theme="monokai"))

    if mode == ApprovalMode.ASK:
        # In ask mode, diffs are shown but batch-approved with the plan
        return True

    # Paranoid: approve each file individually
    if not _prompt_user(f"Apply this diff to {filepath}?"):
        raise ApprovalDenied(f"Diff rejected for {filepath}")
    return True


def approve_command(cmd: str, mode: str | ApprovalMode) -> bool:
    """Show a shell command and request approval."""
    mode = ApprovalMode(mode)
    if mode == ApprovalMode.AUTO:
        return True

    console.print(f"\n[bold]Command:[/bold] {cmd}")

    if mode == ApprovalMode.ASK:
        # In ask mode, commands shown in plan are batch-approved
        return True

    # Paranoid: approve each command
    if not _prompt_user("Execute this command?"):
        raise ApprovalDenied(f"Command rejected: {cmd}")
    return True
