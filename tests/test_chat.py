"""Tests for the interactive chat module."""
import pytest

from mca.orchestrator.chat import (
    _filter_tool_defs,
    _trim_messages,
    _READ_ONLY_TOOLS,
    _WRITE_TOOLS,
    _TRIM_THRESHOLD,
    _TRIM_TARGET,
)


class TestFilterToolDefs:
    def _make_def(self, name: str) -> dict:
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    def test_filters_to_allowed(self):
        defs = [self._make_def("read_file"), self._make_def("write_file"), self._make_def("done")]
        allowed = frozenset({"read_file"})
        filtered = _filter_tool_defs(defs, allowed)
        assert len(filtered) == 1
        assert filtered[0]["function"]["name"] == "read_file"

    def test_empty_allowed(self):
        defs = [self._make_def("read_file"), self._make_def("write_file")]
        filtered = _filter_tool_defs(defs, frozenset())
        assert len(filtered) == 0

    def test_all_allowed(self):
        defs = [self._make_def("read_file"), self._make_def("write_file")]
        allowed = frozenset({"read_file", "write_file"})
        filtered = _filter_tool_defs(defs, allowed)
        assert len(filtered) == 2

    def test_read_only_tools_defined(self):
        """Verify that read-only tools are a known set."""
        assert "read_file" in _READ_ONLY_TOOLS
        assert "list_files" in _READ_ONLY_TOOLS
        assert "search" in _READ_ONLY_TOOLS
        assert "write_file" not in _READ_ONLY_TOOLS
        assert "edit_file" not in _READ_ONLY_TOOLS

    def test_write_tools_defined(self):
        """Verify that write tools are a known set."""
        assert "write_file" in _WRITE_TOOLS
        assert "replace_in_file" in _WRITE_TOOLS
        assert "edit_file" in _WRITE_TOOLS
        assert "read_file" not in _WRITE_TOOLS


class TestTrimMessages:
    def test_no_trim_under_threshold(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        result = _trim_messages(messages)
        assert len(result) == 10

    def test_trims_at_threshold(self):
        messages = [{"role": "system", "content": "system"}]
        messages += [{"role": "user", "content": f"msg {i}"} for i in range(_TRIM_THRESHOLD + 5)]
        result = _trim_messages(messages)
        assert len(result) <= _TRIM_TARGET

    def test_preserves_system_message(self):
        messages = [{"role": "system", "content": "I am the system"}]
        messages += [{"role": "user", "content": f"msg {i}"} for i in range(_TRIM_THRESHOLD + 5)]
        result = _trim_messages(messages)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "I am the system"

    def test_keeps_recent_messages(self):
        messages = [{"role": "system", "content": "sys"}]
        messages += [{"role": "user", "content": f"msg {i}"} for i in range(60)]
        result = _trim_messages(messages)
        # The last message should be preserved
        assert result[-1]["content"] == "msg 59"
