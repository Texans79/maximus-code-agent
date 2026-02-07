"""Orchestrator loop: plan → edit → run → fix → verify."""
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from openai import OpenAI

from mca.config import Config
from mca.log import console, get_logger
from mca.orchestrator.approval import ApprovalDenied, approve_command, approve_diff, approve_plan
from mca.tools.git_ops import GitOps
from mca.tools.safe_fs import SafeFS
from mca.tools.safe_shell import SafeShell, DeniedCommandError
from mca.utils.secrets import redact

log = get_logger("orchestrator")

MAX_ITERATIONS = 10
SYSTEM_PROMPT = """\
You are Maximus Code Agent (MCA), an expert AI coding assistant.
You operate on a workspace directory. You have these tools:

1. read_file(path) — read a file (relative to workspace)
2. write_file(path, content) — create or overwrite a file
3. edit_file(path, diff) — apply a unified diff patch to an existing file
4. search(pattern, glob) — grep for a pattern in workspace files
5. list_files() — list workspace file tree
6. run_command(cmd) — execute a shell command in the workspace
7. done(summary) — signal task completion with a summary

RULES:
- ALWAYS prefer edit_file with diffs over write_file for existing files.
- For new files, use write_file.
- After code changes, run tests to verify. If tests fail, fix and retry.
- Generate minimal tests if none exist.
- Be concise. Explain what you change and why.
- Never leak secrets or environment variables.

Respond with a JSON array of tool calls:
[{"tool": "tool_name", "args": {"key": "value"}}]

Only respond with the JSON array, no other text.
"""


def _build_context(fs: SafeFS) -> str:
    """Build repo context for the LLM."""
    tree = fs.tree(max_depth=3)
    tree_str = "\n".join(f"  {f}" for f in tree[:100])
    if len(tree) > 100:
        tree_str += f"\n  … and {len(tree) - 100} more files"
    return f"Workspace files:\n{tree_str}"


def _call_llm(client: OpenAI, model: str, messages: list[dict], config: Config) -> str:
    """Call the LLM and return the assistant message content."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return json.dumps([{"tool": "done", "args": {"summary": f"LLM error: {e}"}}])


def _parse_tool_calls(text: str) -> list[dict]:
    """Extract tool calls from LLM response."""
    text = text.strip()
    # Try to find JSON array in the response
    if not text.startswith("["):
        # Look for JSON block
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
    fs: SafeFS,
    shell: SafeShell,
    approval_mode: str,
) -> dict[str, Any]:
    """Execute a single tool call. Returns result dict."""
    try:
        if tool == "read_file":
            content = fs.read(args["path"])
            # Truncate for context window
            if len(content) > 8000:
                content = content[:8000] + "\n… [truncated]"
            return {"ok": True, "content": content}

        elif tool == "write_file":
            path = args["path"]
            content = args["content"]
            diff = fs.generate_diff(path, content)
            if diff:
                approve_diff(path, diff, approval_mode)
            fs.write_force(path, content)
            return {"ok": True, "wrote": path, "bytes": len(content)}

        elif tool == "edit_file":
            path = args["path"]
            diff = args["diff"]
            approve_diff(path, diff, approval_mode)
            ok = fs.apply_diff(path, diff)
            return {"ok": ok, "edited": path}

        elif tool == "search":
            results = fs.search(args["pattern"], args.get("glob", "**/*"))
            # Limit results
            truncated = len(results) > 50
            results = results[:50]
            return {"ok": True, "matches": results, "truncated": truncated}

        elif tool == "list_files":
            tree = fs.tree(max_depth=args.get("depth", 3))
            return {"ok": True, "files": tree[:200]}

        elif tool == "run_command":
            cmd = args["cmd"]
            approve_command(cmd, approval_mode)
            result = shell.run(cmd)
            return {
                "ok": result.exit_code == 0,
                "exit_code": result.exit_code,
                "stdout": redact(result.stdout[:5000]),
                "stderr": redact(result.stderr[:2000]),
            }

        elif tool == "done":
            return {"ok": True, "done": True, "summary": args.get("summary", "")}

        else:
            return {"ok": False, "error": f"Unknown tool: {tool}"}

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
    """Run the full orchestrator loop for a task."""
    log.info("Starting task: %s", task)
    log.info("Workspace: %s, Mode: %s", workspace, approval_mode)

    # Initialize tools
    fs = SafeFS(workspace)
    shell = SafeShell(
        workspace=workspace,
        denylist=config.shell.as_dict().get("denylist", []),
        allowlist=config.shell.as_dict().get("allowlist", []),
        timeout=config.shell.timeout,
    )
    git = GitOps(workspace)

    # Git checkpoint
    checkpoint_tag = None
    if config.git.auto_checkpoint:
        git.ensure_repo()
        checkpoint_tag = git.checkpoint(f"MCA start: {task[:60]}")
        console.print(f"[dim]Git checkpoint: {checkpoint_tag}[/dim]")

    # LLM client
    client = OpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
    )
    model = config.llm.model

    # Build initial messages
    context = _build_context(fs)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{context}\n\nTask: {task}"},
    ]

    # Show plan approval in ask mode
    if approval_mode in ("ask", "paranoid"):
        # First LLM call to get the plan
        console.print("[info]Generating plan…[/info]")
        plan_response = _call_llm(client, model, messages, config)
        try:
            approve_plan(f"Task: {task}\n\nLLM proposed actions:\n{plan_response[:2000]}", approval_mode)
        except ApprovalDenied:
            if checkpoint_tag:
                git.rollback()
            return {"success": False, "error": "Plan rejected by user"}
        # Add plan response to conversation
        messages.append({"role": "assistant", "content": plan_response})
    else:
        # Auto mode — just get the first response
        plan_response = _call_llm(client, model, messages, config)
        messages.append({"role": "assistant", "content": plan_response})

    # Iteration loop
    last_summary = ""
    success = False

    for iteration in range(MAX_ITERATIONS):
        console.print(f"\n[bold cyan]── Iteration {iteration + 1}/{MAX_ITERATIONS} ──[/bold cyan]")

        # Parse tool calls from last LLM response
        last_content = messages[-1]["content"] if messages[-1]["role"] == "assistant" else plan_response
        tool_calls = _parse_tool_calls(last_content)

        # Execute each tool call
        results: list[dict] = []
        done = False
        for tc in tool_calls:
            tool_name = tc.get("tool", "")
            tool_args = tc.get("args", {})
            console.print(f"  [bold]→ {tool_name}[/bold]({', '.join(f'{k}={v!r}' for k, v in list(tool_args.items())[:3])})")

            result = _execute_tool(tool_name, tool_args, fs, shell, approval_mode)
            results.append({"tool": tool_name, "result": result})

            if result.get("done"):
                done = True
                last_summary = result.get("summary", "")
                break

        if done:
            success = True
            break

        # Feed results back to LLM
        feedback = json.dumps(results, indent=2, default=str)
        messages.append({"role": "user", "content": f"Tool results:\n{feedback}"})

        # Get next LLM action
        console.print("[dim]  Thinking…[/dim]")
        next_response = _call_llm(client, model, messages, config)
        messages.append({"role": "assistant", "content": next_response})

    # Final summary
    if success:
        console.print(f"\n[success]Summary: {last_summary}[/success]")
        if checkpoint_tag:
            git.checkpoint(f"MCA done: {task[:60]}")
    else:
        console.print("[error]Max iterations reached without completion.[/error]")
        if checkpoint_tag:
            console.print("[warn]Rolling back…[/warn]")
            git.rollback()

    return {
        "success": success,
        "summary": last_summary,
        "iterations": min(iteration + 1, MAX_ITERATIONS) if 'iteration' in dir() else 0,
        "shell_history": [
            {"cmd": r.command, "exit": r.exit_code, "duration": r.duration_s}
            for r in shell.history
        ],
    }
