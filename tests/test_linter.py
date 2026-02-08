"""Tests for LinterFormatter tool."""
import json

import pytest

from mca.tools.linter import LinterFormatter
from mca.tools.safe_shell import SafeShell


@pytest.fixture
def python_workspace(tmp_path):
    (tmp_path / "app.py").write_text("import os\nx=1\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="test"\n')
    return tmp_path


class TestParsing:
    def test_parse_ruff_json(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        sample = json.dumps([
            {
                "filename": "app.py",
                "location": {"row": 1, "column": 1},
                "code": "F401",
                "message": "os imported but unused",
                "fix": {"applicability": "safe"},
            }
        ])
        issues = linter._parse_ruff_json(sample)
        assert len(issues) == 1
        assert issues[0]["code"] == "F401"
        assert issues[0]["file"] == "app.py"
        assert issues[0]["line"] == 1

    def test_parse_eslint_json(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        sample = json.dumps([{
            "filePath": "index.js",
            "messages": [
                {"line": 5, "column": 1, "severity": 2, "ruleId": "no-unused-vars",
                 "message": "x is defined but never used"},
            ],
        }])
        issues = linter._parse_eslint_json(sample)
        assert len(issues) == 1
        assert issues[0]["severity"] == "error"
        assert issues[0]["code"] == "no-unused-vars"

    def test_parse_ruff_invalid_json(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        assert linter._parse_ruff_json("not json") == []

    def test_parse_eslint_invalid_json(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        assert linter._parse_eslint_json("not json") == []


class TestActions:
    def test_detect_linters_action(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        result = linter.execute("detect_linters", {})
        assert result.ok
        assert isinstance(result.data["linters"], list)

    def test_verify(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        assert linter.verify().ok

    def test_unknown_action(self, python_workspace):
        shell = SafeShell(python_workspace)
        linter = LinterFormatter(shell, python_workspace)
        with pytest.raises(ValueError):
            linter.execute("bad_action", {})
