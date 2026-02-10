"""Telegram bot for remote MCA control + free-form chat."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mca.config import Config
from mca.log import get_logger

log = get_logger("telegram")

# Chat settings
_MAX_TOOL_ROUNDS = 5
_MAX_HISTORY = 40
_TRIM_THRESHOLD = 50

# Read-only tools allowed in Telegram chat
_CHAT_TOOLS = frozenset({
    "read_file", "list_files", "search", "run_command",
    "git_log", "git_diff", "memory_search", "run_tests",
    "system_info", "index_repo",
    "query_db", "list_tables", "describe_table",
})


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

    # Per-chat conversation history: {chat_id: [messages]}
    chat_histories: dict[int, list[dict]] = {}

    # Shared resources for chat (lazy-init)
    _chat_resources: dict[str, Any] = {}

    def _get_chat_resources():
        """Lazy-init LLM client, registry, and store for chat."""
        if "client" not in _chat_resources:
            from mca.llm.client import get_client
            from mca.tools.registry import build_registry
            from mca.orchestrator.prompts import build_chat_system_prompt

            ws = Path(config.workspace).resolve()
            store = None
            try:
                from mca.memory.base import get_store
                store = get_store(config)
            except Exception:
                pass

            client = get_client(config)
            registry = build_registry(ws, config, memory_store=store)
            all_defs = registry.tool_definitions()
            tool_defs = [
                d for d in all_defs
                if d.get("function", {}).get("name") in _CHAT_TOOLS
            ]
            system_prompt = build_chat_system_prompt(workspace_name=ws.name)

            _chat_resources["client"] = client
            _chat_resources["registry"] = registry
            _chat_resources["tool_defs"] = tool_defs
            _chat_resources["system_prompt"] = system_prompt
            _chat_resources["workspace"] = ws
        return _chat_resources

    def _check_user(update: Update) -> bool:
        """Verify user is allowed."""
        if not allowed_users:
            return True
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
            "/memory <query> - Search past tasks\n"
            "/logs - Recent log entries\n"
            "/rollback - Rollback last change\n"
            "/clear - Clear chat history\n\n"
            "Or just type a message to chat with me.\n"
            "I can read your codebase, search files, run commands, and query the database."
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

        from mca.orchestrator.loop import run_task
        ws = Path(config.workspace).resolve()

        def _run_sync():
            return run_task(task=task, workspace=ws, config=config, approval_mode="auto")

        try:
            result = await asyncio.to_thread(_run_sync)
            if result.get("success"):
                summary = result.get("summary", "")
                iters = result.get("iterations", 0)
                tools = result.get("tool_calls_made", 0)
                await update.message.reply_text(
                    f"Task completed!\n\n{summary}\n\n"
                    f"Iterations: {iters} | Tool calls: {tools}"
                )
            else:
                err = result.get("error", result.get("summary", "unknown"))
                await update.message.reply_text(f"Task failed: {err}")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        query = " ".join(context.args) if context.args else ""
        if not query:
            await update.message.reply_text("Usage: /memory <search query>")
            return
        try:
            from mca.memory.base import get_store
            from mca.memory.recall import recall_similar
            from mca.memory.embeddings import get_embedder
            store = get_store(config)
            embedder = get_embedder(config)
            results = recall_similar(store, embedder, query, limit=5)
            embedder.close()
            store.close()
            if not results:
                await update.message.reply_text("No matching entries found.")
                return
            lines = []
            for r in results:
                cat = r.get("category", "general")
                content = r["content"][:200]
                lines.append(f"[{cat}] {content}")
            await update.message.reply_text("\n\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f"Memory search error: {e}")

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

    async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _check_user(update):
            return
        chat_id = update.effective_chat.id
        chat_histories.pop(chat_id, None)
        await update.message.reply_text("Chat history cleared.")

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle free-form text messages — chat mode with tools."""
        if not _check_user(update):
            return

        user_text = update.message.text
        if not user_text:
            return

        chat_id = update.effective_chat.id

        # Lazy-init chat resources
        try:
            res = _get_chat_resources()
        except Exception as e:
            await update.message.reply_text(f"Chat init failed: {e}")
            return

        client = res["client"]
        registry = res["registry"]
        tool_defs = res["tool_defs"]
        system_prompt = res["system_prompt"]

        # Get or create conversation history for this chat
        if chat_id not in chat_histories:
            chat_histories[chat_id] = [{"role": "system", "content": system_prompt}]
        messages = chat_histories[chat_id]

        # Add user message
        messages.append({"role": "user", "content": user_text})

        # Trim if too long
        if len(messages) > _TRIM_THRESHOLD:
            system = messages[:1]
            messages = system + messages[-(_MAX_HISTORY - 1):]
            chat_histories[chat_id] = messages

        # Send typing indicator
        await update.effective_chat.send_action("typing")

        # Tool loop — up to N rounds
        final_text = ""
        tool_log_lines = []

        def _chat_sync():
            nonlocal final_text, tool_log_lines
            for _round in range(_MAX_TOOL_ROUNDS):
                resp = client.chat(
                    messages=messages,
                    tools=tool_defs,
                    temperature=config.llm.temperature,
                    max_tokens=config.llm.max_tokens,
                )

                if not resp.tool_calls:
                    final_text = resp.content or "(no response)"
                    messages.append({"role": "assistant", "content": final_text})
                    break

                # Build assistant message with tool_calls
                assistant_msg: dict[str, Any] = {"role": "assistant", "content": resp.content or ""}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in resp.tool_calls
                ]
                messages.append(assistant_msg)

                # Execute tool calls
                for tc in resp.tool_calls:
                    if tc.name not in _CHAT_TOOLS:
                        result = {"ok": False, "error": f"Tool '{tc.name}' not available in Telegram chat."}
                        tool_log_lines.append(f"Blocked: {tc.name}")
                    elif tc.name == "done":
                        result = {"ok": False, "error": "done() not available in chat mode."}
                    else:
                        args_short = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(tc.arguments.items())[:2])
                        tool_log_lines.append(f"{tc.name}({args_short})")
                        try:
                            tool_result = registry.dispatch(tc.name, tc.arguments)
                            result = tool_result.to_dict()
                        except Exception as e:
                            result = {"ok": False, "error": str(e)}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    })
            else:
                final_text = "(max tool rounds reached)"
                messages.append({"role": "assistant", "content": final_text})

        try:
            await asyncio.to_thread(_chat_sync)
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        # Build response — show tool calls if any, then the answer
        reply_parts = []
        if tool_log_lines:
            tools_summary = "\n".join(f"  > {l}" for l in tool_log_lines)
            reply_parts.append(f"Tools used:\n{tools_summary}\n")
        reply_parts.append(final_text)

        reply = "\n".join(reply_parts)

        # Telegram has a 4096 char limit per message
        if len(reply) > 4000:
            # Send in chunks
            for i in range(0, len(reply), 4000):
                chunk = reply[i:i + 4000]
                await update.message.reply_text(chunk)
        else:
            await update.message.reply_text(reply)

    # Build application
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("rollback", cmd_rollback))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Telegram bot starting (chat enabled)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
