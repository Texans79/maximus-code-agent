"""Telegram bot for remote MCA control."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mca.config import Config
from mca.log import get_logger

log = get_logger("telegram")


def start_bot(config: Config) -> None:
    """Start the Telegram bot (blocking)."""
    try:
        from telegram import Update
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError:
        raise ImportError("Install telegram support: pip install 'maximus-code-agent[telegram]'")

    token = config.telegram.token
    allowed_users = config.telegram.as_dict().get("allowed_users", [])

    def _check_user(update: Update) -> bool:
        """Verify user is allowed."""
        if not allowed_users:
            return True  # No restrictions if empty
        user_id = update.effective_user.id if update.effective_user else 0
        username = update.effective_user.username if update.effective_user else ""
        return user_id in allowed_users or username in allowed_users

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            await update.message.reply_text("Unauthorized.")
            return
        await update.message.reply_text(
            "Maximus Code Agent Bot\n\n"
            "Commands:\n"
            "/status - System telemetry\n"
            "/run <task> - Run a coding task\n"
            "/logs - Recent log entries\n"
            "/rollback - Rollback last change\n"
            "/approve - Approve pending action\n"
            "/deny - Deny pending action\n"
        )

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        from mca.telemetry.collectors import collect_all
        data = collect_all()

        cpu = data["cpu"]
        ram = data["ram"]
        lines = [
            f"*CPU:* {cpu['name']}",
            f"  Load: {cpu['load_1m']:.1f}%  Cores: {cpu['cores_physical']}P/{cpu['cores_logical']}L",
            f"*RAM:* {ram['used_gb']:.1f}/{ram['total_gb']:.1f} GB ({ram['percent']:.0f}%)",
        ]
        for d in data["disks"][:3]:
            lines.append(f"*Disk {d['mount']}:* {d['used_gb']:.0f}/{d['total_gb']:.0f} GB ({d['percent']:.0f}%)")
        for gpu in data.get("gpus", []):
            lines.append(
                f"*GPU {gpu['index']}:* {gpu['name']} | {gpu['temp_c']}°C | "
                f"{gpu['util_percent']}% | {gpu['mem_used_mb']}/{gpu['mem_total_mb']}MB | {gpu['power_w']}W"
            )
        for nv in data.get("nvme", []):
            lines.append(f"*NVMe {nv['device']}:* {nv['temp_c']}°C")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        task = " ".join(context.args) if context.args else ""
        if not task:
            await update.message.reply_text("Usage: /run <task description>")
            return
        await update.message.reply_text(f"Starting task: {task}\n(Running in auto mode)")

        # Run in background
        from mca.orchestrator.loop import run_task
        ws = Path(config.workspace).resolve()

        try:
            result = run_task(task=task, workspace=ws, config=config, approval_mode="auto")
            if result.get("success"):
                await update.message.reply_text(f"Task completed!\n\n{result.get('summary', '')}")
            else:
                await update.message.reply_text(f"Task failed: {result.get('error', 'unknown')}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        from mca.tools.git_ops import GitOps
        ws = Path(config.workspace).resolve()
        git = GitOps(ws)
        ref = git.rollback()
        if ref:
            await update.message.reply_text(f"Rolled back to {ref}")
        else:
            await update.message.reply_text("No checkpoint found to rollback.")

    async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        log_file = Path(".mca/logs/mca.jsonl")
        if log_file.exists():
            lines = log_file.read_text().strip().splitlines()[-20:]
            await update.message.reply_text("```\n" + "\n".join(lines[-10:]) + "\n```", parse_mode="Markdown")
        else:
            await update.message.reply_text("No logs found.")

    async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        await update.message.reply_text("Approved. (Approval queue not yet connected.)")

    async def cmd_deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        await update.message.reply_text("Denied. (Approval queue not yet connected.)")

    # Build application
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("rollback", cmd_rollback))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))

    log.info("Telegram bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
