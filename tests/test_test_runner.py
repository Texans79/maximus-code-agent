"""Tests for TestRunner tool."""
import pytest

from mca.tools.test_runner import TestRunner
from mca.tools.safe_shell import SafeShell


@pytest.fixture
def pytest_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\n\n[tool.pytest.ini_options]\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_example.py").write_text(
        "def test_pass():\n    assert True\n"
    )
    return tmp_path


@pytest.fixture
def jest_workspace(tmp_path):
    (tmp_path / "jest.config.js").write_text("module.exports = {};")
    (tmp_path / "package.json").write_text('{"name": "test", "dependencies": {"jest": "^29"}}')
    return tmp_path


@pytest.fixture
def empty_workspace(tmp_path):
    return tmp_path


class TestDetection:
    def test_detect_pytest(self, pytest_workspace):
        shell = SafeShell(pytest_workspace)
        runner = TestRunner(shell, pytest_workspace)
        detected = runner.detect_framework()
        assert detected is not None
        assert detected[0] == "pytest"

    def test_detect_jest(self, jest_workspace):
        shell = SafeShell(jest_workspace)
        runner = TestRunner(shell, jest_workspace)
        detected = runner.detect_framework()
        assert detected is not None
        assert detected[0] == "jest"

    def test_detect_none(self, empty_workspace):
        shell = SafeShell(empty_workspace)
        runner = TestRunner(shell, empty_workspace)
        assert runner.detect_framework() is None

    def test_detect_fallback_tests_dir(self, tmp_path):
        (tmp_path / "tests").mkdir()
        shell = SafeShell(tmp_path)
        runner = TestRunner(shell, tmp_path)
        detected = runner.detect_framework()
        assert detected is not None
        assert detected[0] == "pytest"


class TestParsing:
    def test_parse_pytest_output(self, pytest_workspace):
        shell = SafeShell(pytest_workspace)
        runner = TestRunner(shell, pytest_workspace)
        summary = runner._parse_pytest(
            "5 passed, 2 failed, 1 error in 3.45s", ""
        )
        assert summary["passed"] == 5
        assert summary["failed"] == 2
        assert summary["errors"] == 1
        assert summary["duration_s"] == 3.45

    def test_parse_pytest_only_passed(self, pytest_workspace):
        shell = SafeShell(pytest_workspace)
        runner = TestRunner(shell, pytest_workspace)
        summary = runner._parse_pytest("10 passed in 1.20s", "")
        assert summary["passed"] == 10
        assert summary["failed"] == 0

    def test_parse_jest_output(self, jest_workspace):
        shell = SafeShell(jest_workspace)
        runner = TestRunner(shell, jest_workspace)
        summary = runner._parse_jest("Tests:  1 failed, 2 skipped, 5 passed")
        assert summary["passed"] == 5
        assert summary["failed"] == 1
        assert summary["skipped"] == 2


class TestExecution:
    def test_detect_framework_action(self, pytest_workspace):
        shell = SafeShell(pytest_workspace)
        runner = TestRunner(shell, pytest_workspace)
        result = runner.execute("detect_test_framework", {})
        assert result.ok
        assert result.data["framework"] == "pytest"

    def test_detect_no_framework(self, empty_workspace):
        shell = SafeShell(empty_workspace)
        runner = TestRunner(shell, empty_workspace)
        result = runner.execute("detect_test_framework", {})
        assert not result.ok

    def test_verify(self, pytest_workspace):
        shell = SafeShell(pytest_workspace)
        runner = TestRunner(shell, pytest_workspace)
        result = runner.verify()
        assert result.ok
