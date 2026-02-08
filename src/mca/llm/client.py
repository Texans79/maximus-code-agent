"""LLM client for vLLM OpenAI-compatible endpoint.

Supports chat completions with tool/function calling, retry with
exponential backoff, and timeout handling.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from mca.log import get_logger

log = get_logger("llm")


@dataclass
class ToolCall:
    """A single tool call parsed from the LLM response."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Structured response from the LLM."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = ""


class LLMError(Exception):
    """Raised when the LLM returns an error or is unreachable."""


class LLMClient:
    """Client for vLLM OpenAI-compatible chat completions API.

    Reads configuration from environment variables with fallbacks:
        VLLM_BASE_URL  → http://localhost:8000/v1
        VLLM_MODEL     → config.llm.model
        VLLM_API_KEY   → not-needed
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("VLLM_BASE_URL")
            or "http://localhost:8000/v1"
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("VLLM_MODEL")
            or "Qwen/Qwen2.5-72B-Instruct-AWQ"
        )
        self.api_key = (
            api_key
            or os.environ.get("VLLM_API_KEY")
            or "not-needed"
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=timeout, write=30.0, pool=10.0),
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request with optional tool definitions.

        Retries with exponential backoff on transient failures.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return self._parse_response(data)

            except httpx.TimeoutException as e:
                last_err = e
                log.warning("LLM request timeout (attempt %d/%d): %s",
                            attempt + 1, self.max_retries, e)
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    last_err = e
                    log.warning("LLM server error %d (attempt %d/%d)",
                                e.response.status_code, attempt + 1, self.max_retries)
                else:
                    raise LLMError(f"LLM request failed: {e.response.status_code} {e.response.text}") from e
            except httpx.ConnectError as e:
                last_err = e
                log.warning("LLM connection failed (attempt %d/%d): %s",
                            attempt + 1, self.max_retries, e)

            if attempt < self.max_retries - 1:
                delay = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                log.info("Retrying in %.1fs...", delay)
                time.sleep(delay)

        raise LLMError(f"LLM request failed after {self.max_retries} attempts: {last_err}")

    def ping(self) -> dict[str, Any]:
        """Verify the LLM endpoint is reachable. Returns model info."""
        try:
            resp = self._client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            model_ids = [m.get("id", "") for m in models]
            return {
                "ok": True,
                "models": model_ids,
                "endpoint": self.base_url,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "endpoint": self.base_url}

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        """Parse OpenAI-format response into LLMResponse."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        # Parse tool calls if present
        tool_calls = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            args_str = func.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                args = {"raw": args_str}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=args,
            ))

        # Also try to parse JSON tool calls from content (for models that
        # return tool calls inline instead of in the tool_calls field)
        content = message.get("content", "") or ""
        if not tool_calls and content.strip().startswith("["):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    for i, item in enumerate(parsed):
                        if isinstance(item, dict) and "tool" in item:
                            tool_calls.append(ToolCall(
                                id=f"inline-{i}",
                                name=item["tool"],
                                arguments=item.get("args", {}),
                            ))
            except json.JSONDecodeError:
                pass

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
            model=data.get("model", ""),
            finish_reason=choice.get("finish_reason", ""),
        )

    def close(self) -> None:
        self._client.close()


def get_client(config: Any = None) -> LLMClient:
    """Factory: create LLMClient from config or env vars."""
    if config and hasattr(config, "llm"):
        return LLMClient(
            base_url=config.llm.get("base_url"),
            model=config.llm.get("model"),
            api_key=config.llm.get("api_key"),
        )
    return LLMClient()
