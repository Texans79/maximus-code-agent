"""OpenAI-compatible API server for MCA.

Exposes MCA's chat engine (LLM + tool execution) as a /v1/chat/completions
endpoint so Open WebUI and other OpenAI-compatible clients can use it.

Usage:
    mca serve --port 8001 --workspace ~/my-project
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Maximus Code Agent API", version="1.0.0")

# Shared resources (lazy-init on first request)
_resources: dict[str, Any] = {}


def init_resources(workspace: Path | None = None, config: Any = None) -> None:
    """Pre-initialize resources. Called from CLI before starting uvicorn."""
    _resources["workspace"] = workspace
    _resources["config"] = config


def _get_resources() -> dict[str, Any]:
    """Lazy-init LLM client, registry, and tools."""
    if "client" in _resources:
        return _resources

    from mca.config import load_config
    from mca.llm.client import get_client
    from mca.memory.base import get_store
    from mca.orchestrator.prompts import build_chat_system_prompt
    from mca.tools.registry import build_registry

    ws = _resources.get("workspace") or Path(".").resolve()
    config = _resources.get("config") or load_config(str(ws))

    store = None
    try:
        store = get_store(config)
    except Exception:
        pass

    client = get_client(config)
    registry = build_registry(ws, config, memory_store=store)
    all_defs = registry.tool_definitions()

    CHAT_TOOLS = frozenset({
        "read_file", "list_files", "search", "run_command",
        "git_log", "git_diff", "memory_search", "run_tests",
        "system_info", "index_repo",
        "query_db", "list_tables", "describe_table",
    })
    tool_defs = [d for d in all_defs if d.get("function", {}).get("name") in CHAT_TOOLS]

    _resources["client"] = client
    _resources["registry"] = registry
    _resources["tool_defs"] = tool_defs
    _resources["chat_tools"] = CHAT_TOOLS
    _resources["system_prompt"] = build_chat_system_prompt(workspace_name=ws.name)
    _resources["store"] = store

    return _resources


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": "mca",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "maximus",
                "root": "mca",
                "parent": None,
                "permission": [{
                    "id": "modelperm-mca",
                    "object": "model_permission",
                    "created": int(time.time()),
                    "allow_create_engine": False,
                    "allow_sampling": True,
                    "allow_logprobs": False,
                    "allow_search_indices": False,
                    "allow_view": True,
                    "allow_fine_tuning": False,
                    "organization": "*",
                    "group": None,
                    "is_blocking": False,
                }],
            }
        ],
    })


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 4096)
    temperature = body.get("temperature", 0.3)

    res = _get_resources()
    client = res["client"]
    registry = res["registry"]
    tool_defs = res["tool_defs"]
    chat_tools = res["chat_tools"]
    system_prompt = res["system_prompt"]

    # Prepend system prompt if not already present
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": system_prompt}] + messages

    # Tool loop — up to 5 rounds
    MAX_ROUNDS = 5
    tool_log = []

    for _round in range(MAX_ROUNDS):
        resp = client.chat(
            messages=messages,
            tools=tool_defs,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if not resp.tool_calls:
            # Final text response
            content = resp.content or ""
            break

        # Build assistant message with tool_calls
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

        # Execute tool calls
        for tc in resp.tool_calls:
            if tc.name not in chat_tools:
                result = {"ok": False, "error": f"Tool '{tc.name}' not available."}
            elif tc.name == "done":
                result = {"ok": False, "error": "done() not available in chat mode."}
            else:
                args_short = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(tc.arguments.items())[:2])
                tool_log.append(f"{tc.name}({args_short})")
                try:
                    tool_result = registry.dispatch(tc.name, tc.arguments)
                    result = tool_result.to_dict()
                except Exception as e:
                    result = {"ok": False, "error": str(e)}

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })
    else:
        content = "(max tool rounds reached)"

    # Build response with tool usage info
    final_content = content
    if tool_log:
        tools_summary = "\n".join(f"  > {t}" for t in tool_log)
        final_content = f"Tools used:\n{tools_summary}\n\n{content}"

    # Token usage from client
    usage = client.token_usage if hasattr(client, "token_usage") else {}

    return JSONResponse({
        "id": f"chatcmpl-{uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mca",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": final_content,
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0),
        },
    })


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "mca"})
