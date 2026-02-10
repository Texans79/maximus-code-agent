"""Interactive chat mode for MCA.

Provides conversational interaction with tool access, without the
full task lifecycle (no preflight, no done() validation, no git checkpoints).

Usage:
    mca chat -w ~/my-project          # read-only tools
    mca chat -w ~/my-project --write  # read + write tools
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mca.config import Config
from mca.llm.client import LLMClient, ToolCall, get_client
from mca.log import console, get_logger
from mca.orchestrator.prompts import build_chat_system_prompt
from mca.tools.registry import ToolRegistry, build_registry

log = get_logger("chat")

# Max tool rounds per user message (prevents runaway)
_MAX_TOOL_ROUNDS = 5
# Trim to this many messages when conversation grows too long
_TRIM_TARGET = 40
_TRIM_THRESHOLD = 50

# Tools allowed in read-only mode
_READ_ONLY_TOOLS = frozenset({
    "read_file", "list_files", "search", "run_command",
    "git_log", "git_diff", "memory_search", "run_tests",
    "system_info", "index_repo",
    "query_db", "list_tables", "describe_table",
})

# Additional tools enabled with --write
_WRITE_TOOLS = frozenset({
    "write_file", "replace_in_file", "edit_file",
    "git_checkpoint", "memory_add",
})


def _filter_tool_defs(
    tool_defs: list[dict[str, Any]],
    allowed: frozenset[str],
) -> list[dict[str, Any]]:
    """Filter tool definitions to only include allowed actions."""
    return [
        d for d in tool_defs
        if d.get("function", {}).get("name") in allowed
    ]


def _trim_messages(messages: list[dict], target: int = _TRIM_TARGET) -> list[dict]:
    """Trim old messages, keeping the system prompt and recent history."""
    if len(messages) <= _TRIM_THRESHOLD:
        return messages
    # Keep system message + last N messages
    system = [m for m in messages[:1] if m.get("role") == "system"]
    return system + messages[-(target - len(system)):]


def _format_tool_call(tc: ToolCall) -> str:
    """Format a tool call for display."""
    args_parts = []
    for k, v in list(tc.arguments.items())[:2]:
        args_parts.append("{}={!r}".format(k, v))
    return "{}({})".format(tc.name, ", ".join(args_parts))


def run_chat(
    workspace: Path,
    config: Config,
    write_enabled: bool = False,
) -> None:
    """Run an interactive chat session with tool access."""
    client = get_client(config)
    store = None
    try:
        from mca.memory.base import get_store
        store = get_store(config)
    except Exception:
        pass

    registry = build_registry(workspace, config, memory_store=store)
    all_defs = registry.tool_definitions()

    allowed = _READ_ONLY_TOOLS | (_WRITE_TOOLS if write_enabled else frozenset())
    tool_defs = _filter_tool_defs(all_defs, allowed)

    mode_label = "read+write" if write_enabled else "read-only"
    console.print(f"[bold cyan]MCA Chat[/bold cyan] -- {workspace.name} ({mode_label})")
    console.print("[dim]Type 'exit' or 'quit' to leave. '/save <text>' to store a memory.[/dim]\n")

    system_prompt = build_chat_system_prompt(workspace_name=workspace.name)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    try:
        while True:
            try:
                user_input = console.input("[bold green]you>[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                console.print("[dim]Goodbye.[/dim]")
                break

            # /save command -- store to knowledge base
            if user_input.startswith("/save "):
                content = user_input[6:].strip()
                if content and store:
                    try:
                        mid = store.add(content=content, tags=["chat"], project=str(workspace), category="context")
                        console.print(f"[green]Saved to memory: {mid}[/green]")
                    except Exception as e:
                        console.print(f"[red]Save failed: {e}[/red]")
                elif not store:
                    console.print("[warn]Memory store not available.[/warn]")
                else:
                    console.print("[dim]Nothing to save.[/dim]")
                continue

            messages.append({"role": "user", "content": user_input})
            messages = _trim_messages(messages)

            # Tool loop -- up to N rounds of tool calls per user message
            for _round in range(_MAX_TOOL_ROUNDS):
                resp = client.chat(
                    messages=messages,
                    tools=tool_defs,
                    temperature=config.llm.temperature,
                    max_tokens=config.llm.max_tokens,
                )

                if not resp.tool_calls:
                    # Pure text response -- display and break
                    content = resp.content or ""
                    if content:
                        console.print(f"\n[bold cyan]mca>[/bold cyan] {content}\n")
                        messages.append({"role": "assistant", "content": content})
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
                    # Block disallowed tools
                    if tc.name not in allowed:
                        result: dict[str, Any] = {"ok": False, "error": "Tool '{}' not available in {} mode.".format(tc.name, mode_label)}
                        console.print(f"  [red]Blocked: {tc.name}[/red]")
                    elif tc.name == "done":
                        result = {"ok": False, "error": "done() is not available in chat mode."}
                    else:
                        label = _format_tool_call(tc)
                        console.print(f"  [dim]> {label}[/dim]")
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
                console.print("[dim](max tool rounds reached for this message)[/dim]")

    finally:
        client.close()
