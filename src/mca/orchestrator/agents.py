"""Multi-agent review pipeline: Planner → Implementer → Reviewer → Tester."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from openai import OpenAI

from mca.config import Config
from mca.log import console, get_logger
from mca.utils.secrets import redact

log = get_logger("agents")


class Role(str, Enum):
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"


ROLE_PROMPTS = {
    Role.PLANNER: """\
You are the PLANNER agent. Given a task and codebase context:
1. Analyze what needs to change.
2. List files to modify/create.
3. Outline the step-by-step implementation plan.
4. Identify risks and edge cases.
5. Specify what tests are needed.

Respond as JSON: {
  "plan": "...",
  "files_to_modify": ["..."],
  "files_to_create": ["..."],
  "tests_needed": ["..."],
  "risks": ["..."]
}""",

    Role.IMPLEMENTER: """\
You are the IMPLEMENTER agent. Given a plan and codebase context:
1. Write the actual code changes as tool calls.
2. Use edit_file with unified diffs for existing files.
3. Use write_file for new files.
4. Be precise — match the existing code style.
5. After changes, run tests.

Respond with a JSON array of tool calls:
[{"tool": "tool_name", "args": {"key": "value"}}]""",

    Role.REVIEWER: """\
You are the REVIEWER agent. You MUST:
1. Review all code changes for bugs, security issues, and style.
2. Check that tests exist for new functionality. If missing, REQUEST them.
3. Flag any potential issues.
4. Either APPROVE or REQUEST_CHANGES.

Respond as JSON: {
  "verdict": "approve" | "request_changes",
  "issues": [{"severity": "error|warning|info", "file": "...", "description": "..."}],
  "missing_tests": ["description of test needed"],
  "comments": "..."
}""",

    Role.TESTER: """\
You are the TESTER agent. You MUST:
1. Check if tests exist for the changes.
2. If tests are missing, generate minimal but meaningful tests.
3. Run all tests and report results.
4. If tests fail, provide the failure details.

Respond with a JSON array of tool calls to create test files and run them:
[{"tool": "tool_name", "args": {"key": "value"}}]

Always end with a run_command to execute tests.""",
}


@dataclass
class AgentResult:
    """Result from a single agent run."""
    role: Role
    raw_response: str
    parsed: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str = ""


def _call_agent(
    client: OpenAI,
    model: str,
    role: Role,
    context: str,
    task: str,
    prior_results: list[AgentResult],
    config: Config,
) -> AgentResult:
    """Run a single agent with its role prompt."""
    prior_context = ""
    for pr in prior_results:
        prior_context += f"\n--- {pr.role.value.upper()} said ---\n{pr.raw_response[:3000]}\n"

    messages = [
        {"role": "system", "content": ROLE_PROMPTS[role]},
        {"role": "user", "content": f"Codebase:\n{context}\n\nTask: {task}\n{prior_context}"},
    ]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
        )
        content = resp.choices[0].message.content or ""
        # Try to parse JSON
        parsed = {}
        try:
            text = content.strip()
            if text.startswith("{"):
                parsed = json.loads(text)
            elif text.startswith("["):
                parsed = {"tool_calls": json.loads(text)}
        except json.JSONDecodeError:
            pass

        return AgentResult(role=role, raw_response=content, parsed=parsed)
    except Exception as e:
        log.error("%s agent failed: %s", role.value, e)
        return AgentResult(role=role, raw_response="", success=False, error=str(e))


def run_pipeline(
    task: str,
    context: str,
    config: Config,
) -> dict[str, Any]:
    """Run the sequential multi-agent pipeline."""
    client = OpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)
    model = config.llm.model
    results: list[AgentResult] = []

    pipeline = [Role.PLANNER, Role.IMPLEMENTER, Role.REVIEWER, Role.TESTER]

    for role in pipeline:
        console.print(f"\n[bold magenta]═══ {role.value.upper()} ═══[/bold magenta]")

        result = _call_agent(client, model, role, context, task, results, config)
        results.append(result)

        if not result.success:
            console.print(f"[error]{role.value} failed: {result.error}[/error]")
            break

        # Display result
        console.print(f"[dim]{result.raw_response[:500]}[/dim]")

        # Reviewer can block
        if role == Role.REVIEWER:
            verdict = result.parsed.get("verdict", "approve")
            if verdict == "request_changes":
                issues = result.parsed.get("issues", [])
                console.print(f"[warn]Reviewer requested changes ({len(issues)} issues)[/warn]")
                for issue in issues[:5]:
                    console.print(f"  [{issue.get('severity', 'info')}] {issue.get('file', '?')}: {issue.get('description', '')}")

                missing = result.parsed.get("missing_tests", [])
                if missing:
                    console.print(f"[warn]Missing tests: {missing}[/warn]")

    return {
        "pipeline_results": [
            {"role": r.role.value, "success": r.success, "parsed": r.parsed}
            for r in results
        ],
        "completed": len(results) == len(pipeline),
        "reviewer_approved": any(
            r.role == Role.REVIEWER and r.parsed.get("verdict") == "approve"
            for r in results
        ),
    }
