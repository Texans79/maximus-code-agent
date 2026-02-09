"""Orchestrator loop: structured function calling with validation.

Uses ToolRegistry for dispatch (with JSON Schema tool definitions),
LLMClient for inference with the `tools` parameter, and PostgreSQL
for task/step/artifact tracking.
"""
from __future__ import annotations

import json
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from mca.config import Config
from mca.llm.client import LLMClient, LLMResponse, ToolCall, get_client
from mca.log import console, get_logger
from mca.orchestrator.approval import ApprovalDenied, approve_command, approve_diff, approve_plan
from mca.tools.base import ToolResult
from mca.tools.registry import ToolRegistry, build_registry
from mca.tools.safe_fs import SafeFS
from mca.tools.safe_shell import DeniedCommandError
from mca.utils.secrets import redact

log = get_logger("orchestrator")

MAX_ITERATIONS = 15
_CHECKPOINT_EVERY_N = 3  # Auto-checkpoint every N file-changing tool calls


def _detect_failure_pattern(failures: list[dict], min_count: int = 3) -> str | None:
    """Detect repeated failure patterns from recent run metrics.

    Groups failure_reason by first 50 chars and returns the pattern
    if it occurs >= min_count times.
    """
    if not failures:
        return None
    reasons = [
        (f.get("failure_reason") or "")[:50]
        for f in failures
        if f.get("failure_reason")
    ]
    if not reasons:
        return None
    counts = Counter(reasons)
    most_common, count = counts.most_common(1)[0]
    if count >= min_count:
        return most_common
    return None


def _build_system_prompt(registry: ToolRegistry, spike_mode: bool = False) -> str:
    """Build dynamic system prompt — tools are passed structurally, not in text."""
    base = """\
You are Maximus Code Agent (MCA), an expert AI coding assistant.
You operate on a workspace directory using the tools provided.

RULES:
- For editing existing files, prefer replace_in_file (exact text match) over edit_file (diff).
- For new files, use write_file.
- After code changes, ALWAYS run_tests to verify. If tests fail, fix and retry.
- If no tests exist, create minimal tests before calling done.
- Be precise — match the existing code style.
- Never leak secrets or environment variables.
- Call done(summary) ONLY when you have verified changes work (tests pass).
- If tests fail, do NOT call done. Fix the issue and retry."""
    if spike_mode:
        base += """

SPIKE MODE ACTIVE (low confidence):
- Start with the simplest possible approach.
- Test each change immediately before proceeding.
- If unsure, run_tests before making further changes.
- Prefer small, incremental changes over large rewrites."""
    return base


def _build_context(registry: ToolRegistry) -> str:
    """Build repo context from the filesystem tool."""
    fs_tool = registry.get_tool("filesystem")
    if not fs_tool:
        return ""
    result = fs_tool.execute("list_files", {})
    if not result.ok:
        return ""
    files = result.data.get("files", [])
    tree = "\n".join(f"  {f}" for f in files[:100])
    if len(files) > 100:
        tree += f"\n  … and {len(files) - 100} more files"
    return f"Workspace files:\n{tree}"


def _execute_tool(
    tc: ToolCall,
    registry: ToolRegistry,
    approval_mode: str,
) -> dict[str, Any]:
    """Execute a single tool call via the registry with approval checks."""
    try:
        # Approval checks for write/command actions
        if tc.name == "write_file" and approval_mode != "auto":
            approve_diff(tc.arguments.get("path", "?"), "(new file)", approval_mode)
        elif tc.name in ("edit_file", "replace_in_file") and approval_mode != "auto":
            old = tc.arguments.get("old_text", tc.arguments.get("diff", ""))
            approve_diff(tc.arguments.get("path", "?"), old, approval_mode)
        elif tc.name == "run_command" and approval_mode != "auto":
            approve_command(tc.arguments.get("command", tc.arguments.get("cmd", "")), approval_mode)

        result = registry.dispatch(tc.name, tc.arguments)
        return result.to_dict()

    except ApprovalDenied as e:
        return {"ok": False, "error": f"Denied: {e}"}
    except DeniedCommandError as e:
        return {"ok": False, "error": f"Blocked: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _validate_done(tc: ToolCall, tool_history: list[dict]) -> str | None:
    """Validate that done() is legitimate — tests must have passed.

    Returns None if valid, or an error message string if invalid.
    """
    # Look for the most recent test run in tool_history
    last_test = None
    for entry in reversed(tool_history):
        if entry.get("tool") == "run_tests":
            last_test = entry.get("result", {})
            break

    if last_test is None:
        return "You must run tests before calling done. Call run_tests first."

    if not last_test.get("ok", False):
        failed = last_test.get("failed", "?")
        output = last_test.get("output", "")[:500]
        return (
            f"Tests are failing ({failed} failed). Fix the issues before calling done.\n"
            f"Test output: {output}"
        )

    return None  # Valid


def run_task(
    task: str,
    workspace: Path,
    config: Config,
    approval_mode: str = "ask",
) -> dict[str, Any]:
    """Run the full orchestrator loop for a task.

    Uses structured function calling: passes tool JSON schemas to the LLM
    via the `tools` parameter, parses ToolCall objects from the response,
    and sends results back as tool-role messages.
    """
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid4())
    log.info("Starting task: %s (run %s)", task, run_id[:8])
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

    # ── Journal init ─────────────────────────────────────────────────────
    journal = None
    try:
        from mca.journal.writer import JournalWriter
        journal = JournalWriter(store, task_id, run_id, workspace,
                                task_description=task)
        journal.log("start", f"Task: {task[:100]}")
    except Exception as e:
        log.warning("Journal init failed: %s", e)

    # ── Build registry ───────────────────────────────────────────────────
    registry = build_registry(workspace, config, memory_store=store)
    tool_defs = registry.tool_definitions()

    # ── Preflight checks ─────────────────────────────────────────────────
    try:
        from mca.preflight.checks import PreflightRunner
        preflight = PreflightRunner(config, workspace, registry=registry, store=store)
        pf_report = preflight.run_all()
        if journal:
            journal.log(
                "preflight",
                f"{pf_report.passed}✓ {pf_report.warned}! {pf_report.failed}✗",
                pf_report.to_journal_detail(),
            )
        preflight.print_report(pf_report)
        if not pf_report.ready:
            if journal:
                journal.log("error", "Preflight failed — aborting")
                journal.close()
            _finalize_task(store, task_id, False, "Preflight failed")
            return {"success": False, "error": "Preflight failed",
                    "report": pf_report.to_journal_detail()}
    except Exception as e:
        log.warning("Preflight checks failed: %s", e)

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

    # ── Mass fix detection ───────────────────────────────────────────────
    mass_fix_prompt = ""
    try:
        if store and hasattr(store, "conn"):
            from mca.memory.metrics import get_failures
            failures = get_failures(store.conn, days=7)
            pattern = _detect_failure_pattern(failures)
            if pattern:
                mass_fix_prompt = (
                    f"\n\nPATTERN DETECTED: {len(failures)} recent failures with "
                    f"similar cause: {pattern}.\n"
                    "Before proceeding with the task, diagnose the root cause and "
                    "fix the underlying issue. Do not apply individual workarounds."
                )
                if journal:
                    journal.log("mass_fix", f"Pattern detected: {pattern}")
                console.print(f"[warn]Mass fix pattern: {pattern}[/warn]")
    except Exception as e:
        log.debug("Mass fix detection skipped: %s", e)

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

    # ── Confidence scoring ───────────────────────────────────────────────
    confidence_result = None
    spike_mode = False
    try:
        from mca.orchestrator.confidence import calculate_confidence, should_spike
        from mca.memory.embeddings import get_embedder
        conf_embedder = get_embedder(config)
        confidence_result = calculate_confidence(store, conf_embedder, task)
        spike_mode = should_spike(confidence_result)
        conf_embedder.close()
        spike_label = " (SPIKE MODE)" if spike_mode else ""
        console.print(f"[dim]Confidence: {confidence_result.total}/100{spike_label}[/dim]")
        if spike_mode and approval_mode == "auto":
            approval_mode = "ask"
            console.print("[warn]Low confidence → switching to ask mode[/warn]")
    except Exception as e:
        log.debug("Confidence scoring skipped: %s", e)

    # ── Graph recall (structural context) ───────────────────────────────
    graph_context = ""
    try:
        if store and hasattr(store, "conn"):
            from mca.memory.recall import graph_recall
            graph_context = graph_recall(store.conn, str(workspace), task, max_nodes=10)
            if graph_context:
                log.info("Injected graph context (%d chars)", len(graph_context))
    except Exception as e:
        log.debug("Graph recall skipped: %s", e)

    # ── Build initial messages ───────────────────────────────────────────
    system_prompt = _build_system_prompt(registry, spike_mode=spike_mode)
    context = _build_context(registry)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt + mass_fix_prompt},
        {"role": "user", "content": f"{context}{recall_context}{graph_context}\n\nTask: {task}"},
    ]

    # ── Plan approval ────────────────────────────────────────────────────
    if approval_mode in ("ask", "paranoid"):
        console.print("[info]Generating plan…[/info]")
        plan_resp = client.chat(
            messages=messages,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        plan_text = plan_resp.content or "(no plan text)"
        try:
            approve_plan(f"Task: {task}\n\nPlan:\n{plan_text[:2000]}", approval_mode)
        except ApprovalDenied:
            if checkpoint_tag and git_tool:
                git_tool.execute("git_rollback", {})
            _finalize_task(store, task_id, False, "Plan rejected by user")
            _write_run_metrics(store, task_id=task_id, started_at=started_at,
                               success=False, iterations=0, tool_calls=0,
                               files_changed=0, tests_runs=0, lint_runs=0,
                               rollback_used=bool(checkpoint_tag), failure_reason="Plan rejected by user",
                               model=config.llm.model, client=client,
                               confidence_score=confidence_result.total if confidence_result else None,
                               spike_mode=spike_mode)
            client.close()
            return {"success": False, "error": "Plan rejected by user"}
        messages.append({"role": "assistant", "content": plan_text})
        messages.append({"role": "user", "content": "Approved. Proceed with the implementation using tool calls."})

    # ── Record plan step ─────────────────────────────────────────────────
    if store and task_id:
        try:
            step_id = store.add_step(task_id, "plan", agent_role="planner")
            store.update_step(step_id, status="completed",
                              output={"plan": messages[-1]["content"][:2000] if messages else ""})
        except Exception:
            pass
    if journal:
        journal.log("plan", "Plan approved" if approval_mode in ("ask", "paranoid") else "Auto-mode (no plan gate)")

    # ── Iteration loop ───────────────────────────────────────────────────
    last_summary = ""
    success = False
    iteration = 0
    tool_history: list[dict] = []  # Track all tool calls + results
    tests_runs = 0
    lint_runs = 0
    files_changed = 0
    rollback_used = False
    failure_reason = ""
    checkpoint_counter = 0  # For continuous save

    for iteration in range(MAX_ITERATIONS):
        console.print(f"\n[bold cyan]── Iteration {iteration + 1}/{MAX_ITERATIONS} ──[/bold cyan]")

        # ── Call LLM with structured tools ────────────────────────────────
        resp = client.chat(
            messages=messages,
            tools=tool_defs,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )

        # ── Handle pure text response (no tool calls) ─────────────────────
        if not resp.tool_calls:
            content = resp.content or ""
            if content:
                console.print(f"[dim]{content[:300]}[/dim]")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "Please use the available tools to complete the task."})
            else:
                console.print("[warn]LLM returned empty response[/warn]")
                messages.append({"role": "assistant", "content": ""})
                messages.append({"role": "user", "content": "No response received. Please use the available tools."})
            continue

        # ── Build assistant message with tool_calls ───────────────────────
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

        # ── Execute each tool call ────────────────────────────────────────
        done = False
        for tc in resp.tool_calls:
            console.print(
                f"  [bold]→ {tc.name}[/bold]"
                f"({', '.join(f'{k}={v!r}' for k, v in list(tc.arguments.items())[:3])})"
            )

            # ── Validate done() before executing ──────────────────────────
            if tc.name == "done":
                validation_err = _validate_done(tc, tool_history)
                if validation_err:
                    console.print(f"  [warn]Done rejected: {validation_err[:100]}[/warn]")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"ok": False, "error": validation_err}),
                    })
                    # Log the rejected done
                    if store and task_id:
                        try:
                            store.log_tool(task_id, "done", command="REJECTED", exit_code=1)
                        except Exception:
                            pass
                    continue
                # Valid done
                result = _execute_tool(tc, registry, approval_mode)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
                done = True
                last_summary = tc.arguments.get("summary", "")
                break

            # ── Execute the tool ──────────────────────────────────────────
            result = _execute_tool(tc, registry, approval_mode)
            tool_history.append({"tool": tc.name, "args": tc.arguments, "result": result})

            # ── Metric counters ───────────────────────────────────────
            file_changed_this_step = False
            if tc.name == "run_tests":
                tests_runs += 1
            elif tc.name in ("lint", "format_code"):
                lint_runs += 1
            elif tc.name in ("write_file", "edit_file", "replace_in_file"):
                if result.get("ok"):
                    files_changed += 1
                    file_changed_this_step = True

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

            # Log tool execution
            if store and task_id:
                try:
                    store.log_tool(task_id, tc.name,
                                   command=json.dumps(tc.arguments)[:500],
                                   exit_code=0 if result.get("ok") else 1)
                except Exception:
                    pass

            # Journal entry for tool call
            result_summary = "OK" if result.get("ok") else result.get("error", "error")[:100]
            if journal:
                journal.log("tool", f"{tc.name}: {result_summary}",
                            {"args": {k: str(v)[:200] for k, v in list(tc.arguments.items())[:5]}})

            # Continuous save — checkpoint every N file-changing tool calls
            if file_changed_this_step and git_tool and config.git.auto_checkpoint:
                checkpoint_counter += 1
                if checkpoint_counter % _CHECKPOINT_EVERY_N == 0:
                    try:
                        git_tool.execute("git_checkpoint",
                                         {"message": f"MCA step {iteration + 1}: {tc.name}"})
                        if journal:
                            journal.log("checkpoint", f"Auto-saved at iteration {iteration + 1}")
                    except Exception as e:
                        log.debug("Auto-checkpoint failed: %s", e)

            # Print compact result
            if result.get("ok"):
                console.print(f"    [green]OK[/green]")
            else:
                err = result.get("error", "unknown error")
                console.print(f"    [red]FAIL: {err[:100]}[/red]")

        if done:
            success = True
            break

    # ── Finalize ─────────────────────────────────────────────────────────
    try:
        if success:
            console.print(f"\n[bold green]✓ Task complete: {last_summary}[/bold green]")
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
                        diff = diff_result.data.get("diff_stat", "")
                store_outcome(store, embedder, task_id or "unknown", last_summary,
                              outcome="completed", diff=diff, project=str(workspace))
                embedder.close()
            except Exception as e:
                log.debug("Outcome storage skipped: %s", e)

        else:
            failure_reason = "Max iterations reached without completion"
            console.print("[bold red]✗ Max iterations reached without completion.[/bold red]")
            if checkpoint_tag and git_tool:
                console.print("[warn]Rolling back…[/warn]")
                git_tool.execute("git_rollback", {})
                rollback_used = True

        # Journal — final entry
        if journal:
            summary_msg = last_summary if success else failure_reason
            journal.log("done", f"Result: {'success' if success else 'failed'} — {summary_msg}")
            journal.close()

        _finalize_task(store, task_id, success, last_summary)
        _write_run_metrics(store, task_id=task_id, started_at=started_at,
                           success=success, iterations=iteration + 1,
                           tool_calls=len(tool_history), files_changed=files_changed,
                           tests_runs=tests_runs, lint_runs=lint_runs,
                           rollback_used=rollback_used,
                           failure_reason=failure_reason if not success else None,
                           model=config.llm.model, client=client,
                           confidence_score=confidence_result.total if confidence_result else None,
                           spike_mode=spike_mode)
        client.close()

    finally:
        # ── Cleanup (always runs) ────────────────────────────────────────
        try:
            from mca.cleanup.hygiene import CleanupRunner
            cleanup = CleanupRunner(workspace, config)
            cleanup_report = cleanup.run_all()
            if journal and (cleanup_report.orphans_killed or cleanup_report.temps_removed
                            or cleanup_report.log_rotated or cleanup_report.journals_pruned):
                # Journal might already be closed, so just log
                log.info("Cleanup: orphans=%d temps=%d rotated=%s pruned=%d",
                         cleanup_report.orphans_killed, cleanup_report.temps_removed,
                         cleanup_report.log_rotated, cleanup_report.journals_pruned)
        except Exception as e:
            log.debug("Cleanup failed: %s", e)

    return {
        "success": success,
        "summary": last_summary,
        "iterations": iteration + 1,
        "task_id": task_id,
        "run_id": run_id,
        "tool_calls_made": len(tool_history),
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


def _write_run_metrics(
    store, *, task_id: str | None, started_at: datetime,
    success: bool, iterations: int, tool_calls: int,
    files_changed: int, tests_runs: int, lint_runs: int,
    rollback_used: bool, failure_reason: str | None,
    model: str | None, client: LLMClient | None = None,
    confidence_score: int | None = None, spike_mode: bool = False,
) -> None:
    """Write a run_metrics row. Silently skips if store is unavailable."""
    if not store:
        return
    try:
        from mca.memory.metrics import write_metrics
        usage = client.token_usage if client else {}
        write_metrics(
            store.conn,
            task_id=task_id,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            success=success,
            iterations=iterations,
            tool_calls=tool_calls,
            files_changed=files_changed,
            tests_runs=tests_runs,
            lint_runs=lint_runs,
            rollback_used=rollback_used,
            failure_reason=failure_reason,
            model=model,
            token_prompt=usage.get("prompt_tokens", 0),
            token_completion=usage.get("completion_tokens", 0),
            confidence_score=confidence_score,
            spike_mode=spike_mode,
        )
    except Exception as e:
        log.warning("Failed to write run metrics: %s", e)
