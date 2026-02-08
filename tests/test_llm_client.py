"""Tests for LLM client — unit tests with mocked HTTP + live integration."""
import json
import os

import httpx
import pytest

from mca.llm.client import LLMClient, LLMResponse, LLMError, ToolCall


# ── Unit Tests (mocked HTTP) ────────────────────────────────────────────────

class FakeTransport(httpx.BaseTransport):
    """Mock transport that returns canned responses."""

    def __init__(self, responses: list[dict]):
        self._responses = responses
        self._call_count = 0

    def handle_request(self, request):
        idx = min(self._call_count, len(self._responses) - 1)
        self._call_count += 1
        resp_data = self._responses[idx]
        status = resp_data.get("status", 200)
        body = json.dumps(resp_data.get("body", {})).encode()
        return httpx.Response(status, content=body)


def _make_client(responses: list[dict]) -> LLMClient:
    client = LLMClient(base_url="http://fake:8000/v1", model="test-model", max_retries=2)
    client._client = httpx.Client(
        base_url="http://fake:8000/v1",
        transport=FakeTransport(responses),
        timeout=httpx.Timeout(5.0),
    )
    return client


class TestChatParsing:
    def test_simple_content_response(self):
        client = _make_client([{
            "body": {
                "choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}],
                "model": "test-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        }])
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello world"
        assert resp.model == "test-model"
        assert resp.tool_calls == []

    def test_tool_calls_response(self):
        client = _make_client([{
            "body": {
                "choices": [{
                    "message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "main.py"}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "model": "test-model",
                "usage": {},
            }
        }])
        resp = client.chat([{"role": "user", "content": "read main.py"}])
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "main.py"}

    def test_inline_json_tool_calls(self):
        """Models that return tool calls as JSON in content field."""
        client = _make_client([{
            "body": {
                "choices": [{
                    "message": {
                        "content": '[{"tool": "run_command", "args": {"cmd": "ls"}}]',
                    },
                    "finish_reason": "stop",
                }],
                "model": "test-model",
                "usage": {},
            }
        }])
        resp = client.chat([{"role": "user", "content": "list files"}])
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "run_command"
        assert resp.tool_calls[0].arguments == {"cmd": "ls"}


class TestRetry:
    def test_retries_on_server_error(self):
        client = _make_client([
            {"status": 500, "body": {"error": "internal"}},
            {"status": 200, "body": {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "model": "test",
                "usage": {},
            }},
        ])
        resp = client.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"

    def test_raises_on_client_error(self):
        client = _make_client([
            {"status": 400, "body": {"error": "bad request"}},
        ])
        with pytest.raises(LLMError, match="400"):
            client.chat([{"role": "user", "content": "hi"}])


class TestPing:
    def test_ping_success(self):
        client = _make_client([{
            "body": {"data": [{"id": "test-model"}]},
        }])
        result = client.ping()
        assert result["ok"] is True
        assert "test-model" in result["models"]


# ── Live Integration Tests (require running vLLM) ───────────────────────────

def _vllm_available() -> bool:
    try:
        resp = httpx.get("http://localhost:8000/v1/models", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


live = pytest.mark.skipif(not _vllm_available(), reason="vLLM not available")


@live
class TestLiveVLLM:
    def test_ping_real(self):
        client = LLMClient()
        result = client.ping()
        assert result["ok"] is True
        assert len(result["models"]) >= 1
        client.close()

    def test_chat_real(self):
        client = LLMClient()
        resp = client.chat(
            [{"role": "user", "content": "Reply with exactly: HELLO"}],
            temperature=0.1,
            max_tokens=50,
        )
        assert resp.content
        assert len(resp.content) > 0
        client.close()
