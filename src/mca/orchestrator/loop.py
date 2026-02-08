"""Orchestrator loop: plan → implement → review → test.

Uses ToolRegistry for dispatch, LLMClient for inference, and PostgreSQL
for task/step/artifact tracking.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from mca.config import Config
from mca.llm.client import LLMClient, LLMResponse, get_client
from mca.log import console, get_logger
from mca.orchestrator.approval import ApprovalDenied, approve_command, approve_diff, approve_plan
from mca.tools.base import ToolResult
from mca.tools.registry import ToolRegistry, build_registry
from mca.tools.safe_fs import SafeFS
from mca.tools.safe_shell import DeniedCommandError
from mca.utils.secrets import redact

log = get_logger("orchestrator")

MAX_ITERATIONS = 10


def _build_system_prompt(registry: ToolRegistry) -> str:
    """Build dynamic system prompt from registered tools."""
    actions = registry.list_actions()
    tool_desc = "\n".join(f"- {name}: {desc}" for name, desc in actions.items())
    return f"""\
You are Maximus Code Agent (MCA), an expert AI coding assistant.
You operate on a workspace directory. Available actions:

{tool_desc}

RULES:
- ALWAYS prefer edit_file with diffs over write_file for existing files.
- For new files, use write_file.
- After code changes, run tests to verify. If tests fail, fix and retry.
- Generate minimal tests if none exist.
- Be concise. Explain what you change and why.
- Never leak secrets or environment variables.
- Call done(summary) when the task is complete.

Respond with a JSON array of tool calls:
[{{"tool": "action_name", "args": {{"key": "value"}}}}]

Only respond with the JSON array, no other text."""


def _build_context(fs: SafeFS) -> str:
    """Build repo context for the LLM."""
    tree = fs.tree(max_depth=3)
    tree_str = "\n".join(f"  {f}" for f in tree[:100])
    if len(tree) > 100:
        tree_str += f"\n  … and {len(tree) - 100} more files"
    return f"Workspace files:\n{tree_str}"


def _call_llm(client: LLMClient, messages: list[dict], config: Config) -> str:
    """Call the LLM and return the assistant message content."""
    try:
        resp = client.chat(
            messages=messages,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        return resp.content or ""
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return json.dumps([{"tool": "done", "args": {"summary": f"LLM error: {e}"}}])


def _parse_tool_calls(text: str) -> list[dict]:
    """Extract tool calls from LLM response."""
    text = text.strip()
    # Try to find JSON array in the response
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        else:
            return [{"tool": "done", "args": {"summary": text}}]
    try:
        calls = json.loads(text)
        if isinstance(calls, list):
            return calls
        return [calls]
    except json.JSONDecodeError:
        log.warning("Failed to parse LLM response as JSON")
        return [{"tool": "done", "args": {"summary": text}}]


def _execute_tool(
    tool: str,
    args: dict,
    registry: ToolRegistry,
    approval_mode: str,
) -> dict[str, Any]:
    """Execute a tool call via the registry with approval checks."""
    try:
        # Approval checks for write/command actions
        if tool == "write_file" and approval_mode != "auto":
            approve_diff(args.get("path", "?"), "(new file)", approval_mode)
        elif tool == "edit_file" and approval_mode != "auto":
            approve_diff(args.get("path", "?"), args.get("diff", ""), approval_mode)
        elif tool == "run_command" and approval_mode != "auto":
            approve_command(args.get("cmd", ""), approval_mode)

        result = registry.dispatch(tool, args)
        return result.to_dict()

    except ApprovalDenied as e:
        return {"ok": False, "error": f"Denied: {e}"}
    except DeniedCommandError as e:
        return {"ok": False, "error": f"Blocked: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def run_task(
    task: str,
    workspace: Path,
    config: Config,
    approval_mode: str = "ask",
) -> dict[str, Any]:
    """Run the full orchestrator loop for a task.

    Builds the tool registry, connects to LLM, creates a Postgres task
    record, and iterates until done or max iterations.
    """
    log.info("Starting task: %s", task)
    log.info("Workspace: %s, Mode: %s", workspace, approval_mode)

    # ── Initialize memory store ──────────────────────────────────────────
    store = None
    task_id = None
    try:
        from mca.memory.base import get_store
        store = get_store(config)
        task_id = store.create_task(task, workspace=str(workspace))
        store.update_task(task_id, status="running")
        log.info("Task recorded: %s", task_id[:8])
    except Exception as e:
        log.warning("Memory store unavailable: %s", e)

    # ── Build registry ───────────────────────────────────────────────────
    registry = build_registry(workspace, config, memory_store=store)
    system_prompt = _build_system_prompt(registry)

    # ── Git checkpoint ───────────────────────────────────────────────────
    git_tool = registry.get_tool("git")
    checkpoint_tag = None
    if config.git.auto_checkpoint and git_tool:
        try:
            result = git_tool.execute("git_checkpoint", {"message": f"MCA start: {task[:60]}"})
            if result.ok:
                checkpoint_tag = result.data.get("tag", "")
                console.print(f"[dim]Git checkpoint: {checkpoint_tag}[/dim]")
        except Exception as e:
            log.warning("Git checkpoint failed: %s", e)

    # ── LLM client ───────────────────────────────────────────────────────
    client = get_client(config)

    # ── Memory recall (inject similar past work) ─────────────────────────
    recall_context = ""
    try:
        from mca.memory.recall import recall_similar
        from mca.memory.embeddings import get_embedder
        embedder = get_embedder(config)
        similar = recall_similar(store, embedder, task, limit=3)
        embedder.close()
        if similar:
            recall_parts = []
            for s in similar:
                recall_parts.append(f"- [{s.get('category','general')}] {s['content'][:200]}")
            recall_context = "\n\nRelevant past work:\n" + "\n".join(recall_parts)
            log.info("Injected %d recall entries", len(similar))
    except Exception as e:
        log.debug("Memory recall skipped: %s", e)

    # ── Build initial messages ───────────────────────────────────────────
    fs_tool = registry.get_tool("filesystem")
    context = ""
    if fs_tool:
        ctx_result = fs_tool.execute("list_files", {})
        if ctx_result.ok:
            files = ctx_result.data.get("files", [])
            context = "Workspace files:\n" + "\n".join(f"  {f}" for f in files[:100])

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"{context}{recall_context}\n\nTask: {task}"},
    ]

    # ── Plan approval ────────────────────────────────────────────────────
    if approval_mode in ("ask", "paranoid"):
        console.print("[info]Generating plan…[/info]")
        plan_response = _call_llm(client, messages, config)
        try:
            approve_plan(f"Task: {task}\n\nLLM proposed actions:\n{plan_response[:2000]}", approval_mode)
        except ApprovalDenied:
            if checkpoint_tag and git_tool:
                git_tool.execute("git_rollback", {})
            _finalize_task(store, task_id, False, "Plan rejected by user")
            client.close()
            return {"success": False, "error": "Plan rejected by user"}
        messages.append({"role": "assistant", "content": plan_response})
    else:
        plan_response = _call_llm(client, messages, config)
        messages.append({"role": "assistant", "content": plan_response})

    # ── Record plan step ─────────────────────────────────────────────────
    if store and task_id:
        try:
            step_id = store.add_step(task_id, "plan", agent_role="planner")
            store.update_step(step_id, status="completed",
                              output={"plan": plan_response[:2000]})
        except Exception:
            pass

    # ── Iteration loop ───────────────────────────────────────────────────
    last_summary = ""
    success = False
    iteration = 0

    for iteration in range(MAX_ITERATIONS):
        console.print(f"\n[bold cyan]── Iteration {iteration + 1}/{MAX_ITERATIONS} ──[/bold cyan]")

        last_content = messages[-1]["content"] if messages[-1]["role"] == "assistant" else plan_response
        tool_calls = _parse_tool_calls(last_content)

        results: list[dict] = []
        done = False
        for tc in tool_calls:
            tool_name = tc.get("tool", "")
            tool_args = tc.get("args", {})
            console.print(f"  [bold]→ {tool_name}[/bold]"
                          f"({', '.join(f'{k}={v!r}' for k, v in list(tool_args.items())[:3])})")

            result = _execute_tool(tool_name, tool_args, registry, approval_mode)
            results.append({"tool": tool_name, "result": result})

            # Log tool execution
            if store and task_id:
                try:
                    store.log_tool(task_id, tool_name,
                                   command=json.dumps(tool_args)[:500],
                                   exit_code=0 if result.get("ok") else 1)
                except Exception:
                    pass

            if result.get("done"):
                done = True
                last_summary = result.get("summary", "")
                break

        if done:
            success = True
            break

        feedback = json.dumps(results, indent=2, default=str)
        messages.append({"role": "user", "content": f"Tool results:\n{feedback}"})

        console.print("[dim]  Thinking…[/dim]")
        next_response = _call_llm(client, messages, config)
        messages.append({"role": "assistant", "content": next_response})

    # ── Finalize ─────────────────────────────────────────────────────────
    if success:
        console.print(f"\n[success]Summary: {last_summary}[/success]")
        if checkpoint_tag and git_tool:
            git_tool.execute("git_checkpoint", {"message": f"MCA done: {task[:60]}"})

        # Store outcome for future recall
        try:
            from mca.memory.recall import store_outcome
            from mca.memory.embeddings import get_embedder
            embedder = get_embedder(config)
            diff = ""
            if git_tool:
                diff_result = git_tool.execute("git_diff", {})
                if diff_result.ok:
                    diff = diff_result.data.get("diff", "")
            store_outcome(store, embedder, task_id or "unknown", last_summary,
                          outcome="completed", diff=diff, project=str(workspace))
            embedder.close()
        except Exception as e:
            log.debug("Outcome storage skipped: %s", e)

    else:
        console.print("[error]Max iterations reached without completion.[/error]")
        if checkpoint_tag and git_tool:
            console.print("[warn]Rolling back…[/warn]")
            git_tool.execute("git_rollback", {})

    _finalize_task(store, task_id, success, last_summary)
    client.close()

    return {
        "success": success,
        "summary": last_summary,
        "iterations": iteration + 1,
        "task_id": task_id,
    }


def _finalize_task(store, task_id: str | None, success: bool, summary: str) -> None:
    """Update the task record with final status."""
    if not store or not task_id:
        return
    try:
        status = "completed" if success else "failed"
        store.update_task(task_id, status=status,
                          result={"summary": summary, "success": success})
    except Exception as e:
        log.warning("Failed to finalize task: %s", e)
