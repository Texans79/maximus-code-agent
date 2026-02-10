"""Tests for the system prompt module."""
import pytest

from mca.orchestrator.prompts import (
    build_system_prompt,
    build_reflection_prompt,
    build_stuck_nudge,
    build_chat_system_prompt,
)


class TestBuildSystemPrompt:
    def test_contains_identity(self):
        prompt = build_system_prompt()
        assert "Maximus Code Agent" in prompt
        assert "MCA" in prompt

    def test_contains_thinking_discipline(self):
        prompt = build_system_prompt()
        assert "THINKING DISCIPLINE" in prompt
        assert "UNDERSTAND" in prompt
        assert "EXPLORE" in prompt
        assert "PLAN" in prompt
        assert "IMPLEMENT" in prompt
        assert "VERIFY" in prompt

    def test_contains_exploration_strategy(self):
        prompt = build_system_prompt()
        assert "list_files" in prompt
        assert "read_file" in prompt
        assert "search" in prompt

    def test_contains_editing_discipline(self):
        prompt = build_system_prompt()
        assert "NEVER edit a file you haven't read" in prompt
        assert "replace_in_file" in prompt

    def test_contains_error_recovery(self):
        prompt = build_system_prompt()
        assert "ERROR RECOVERY" in prompt
        assert "DIFFERENT approach" in prompt

    def test_contains_completion_rules(self):
        prompt = build_system_prompt()
        assert "done(summary)" in prompt
        assert "run_tests" in prompt

    def test_workspace_name_included(self):
        prompt = build_system_prompt(workspace_name="my-project")
        assert "my-project" in prompt

    def test_workspace_name_empty(self):
        prompt = build_system_prompt(workspace_name="")
        # No extra parens when empty
        assert "()" not in prompt

    def test_iteration_tracking(self):
        prompt = build_system_prompt(iteration=5, max_iterations=25)
        assert "5/25" in prompt

    def test_spike_mode_adds_section(self):
        prompt = build_system_prompt(spike_mode=True)
        assert "SPIKE MODE" in prompt
        assert "low confidence" in prompt

    def test_spike_mode_off_no_section(self):
        prompt = build_system_prompt(spike_mode=False)
        assert "SPIKE MODE" not in prompt

    def test_safety_section(self):
        prompt = build_system_prompt()
        assert "SAFETY" in prompt
        assert "secrets" in prompt.lower()

    def test_code_quality_section(self):
        prompt = build_system_prompt()
        assert "CODE QUALITY" in prompt


class TestBuildReflectionPrompt:
    def test_contains_iteration_info(self):
        prompt = build_reflection_prompt(10, 25, "1. read_file → OK")
        assert "10/25" in prompt
        assert "read_file" in prompt

    def test_contains_assessment_questions(self):
        prompt = build_reflection_prompt(5, 25, "(no tool calls yet)")
        assert "progress" in prompt.lower()
        assert "different approach" in prompt.lower()

    def test_urgency_when_few_iterations_left(self):
        prompt = build_reflection_prompt(21, 25, "...")
        assert "URGENT" in prompt
        assert "4 iterations left" in prompt

    def test_no_urgency_when_plenty_left(self):
        prompt = build_reflection_prompt(5, 25, "...")
        assert "URGENT" not in prompt

    def test_includes_tool_summary(self):
        summary = "  1. list_files() → OK\n  2. read_file(path=main.py) → OK"
        prompt = build_reflection_prompt(5, 25, summary)
        assert "list_files" in prompt
        assert "main.py" in prompt


class TestBuildStuckNudge:
    def test_names_the_repeated_tool(self):
        nudge = build_stuck_nudge("replace_in_file", 3)
        assert "replace_in_file" in nudge
        assert "3 times" in nudge

    def test_suggests_alternatives(self):
        nudge = build_stuck_nudge("edit_file", 3)
        assert "read_file" in nudge
        assert "different" in nudge.lower()

    def test_warns_against_retrying(self):
        nudge = build_stuck_nudge("run_command", 4)
        assert "Do NOT call" in nudge


class TestBuildChatSystemPrompt:
    def test_contains_chat_identity(self):
        prompt = build_chat_system_prompt()
        assert "MCA" in prompt
        assert "chat mode" in prompt

    def test_workspace_name(self):
        prompt = build_chat_system_prompt(workspace_name="my-app")
        assert "my-app" in prompt

    def test_concise_style(self):
        prompt = build_chat_system_prompt()
        assert "direct" in prompt.lower()
        assert "concise" in prompt.lower()

    def test_shorter_than_task_prompt(self):
        chat_prompt = build_chat_system_prompt()
        task_prompt = build_system_prompt()
        assert len(chat_prompt) < len(task_prompt)
