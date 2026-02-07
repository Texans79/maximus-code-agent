"""Tests for config loader."""
import os
import pytest

from mca.config import Config, load_config


class TestConfig:
    def test_defaults(self):
        cfg = load_config()
        assert cfg.approval_mode == "ask"
        assert cfg.llm.model == "Qwen/Qwen2.5-72B-Instruct-AWQ"
        assert cfg.shell.timeout == 120

    def test_attribute_access(self):
        cfg = Config({"a": 1, "b": {"c": 2}})
        assert cfg.a == 1
        assert cfg.b.c == 2

    def test_get(self):
        cfg = Config({"x": 42})
        assert cfg.get("x") == 42
        assert cfg.get("y", "default") == "default"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MCA_APPROVAL_MODE", "paranoid")
        cfg = load_config()
        assert cfg.approval_mode == "paranoid"

    def test_as_dict(self):
        cfg = Config({"a": 1, "b": 2})
        d = cfg.as_dict()
        assert d == {"a": 1, "b": 2}
