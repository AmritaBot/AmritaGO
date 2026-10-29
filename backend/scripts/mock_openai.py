"""Minimal OpenAI-compatible stub for local end-to-end tests.

Serves ``POST /v1/chat/completions`` for both streaming and non-streaming
callers, so the real AmritaCore OpenAI adapter can be exercised without a
provider key.

    .venv/Scripts/python.exe scripts/mock_openai.py        # port 8099

Then point a preset at ``http://127.0.0.1:8099/v1`` with any api_key.

Set ``MOCK_CHUNK_DELAY`` (seconds) to slow the stream down; the reconnect test
needs a provider that is still producing output when the socket drops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI()

CHUNK_DELAY = float(os.environ.get("MOCK_CHUNK_DELAY", "0.01"))
# Optional narration emitted before a tool call, so multi-round text can be tested.
NARRATION = os.environ.get("MOCK_NARRATION", "")

REPLY = (
    "这是来自本地 mock 模型的流式回答。\n\n"
    "它用于验证打字机效果：文本会分成很多小片段推送。\n\n"
    "## 代码\n\n```python\nprint('hello amrita')\n```\n\n"
    "## 公式\n\n$$E = mc^2$$\n\n"
    "## 图表\n\n```mermaid\ngraph TD\n  A[用户] --> B[WebSocket]\n"
    "  B --> C[StreamChannel]\n```\n"
)

# Prefer a real tool so the mock exercises the tool-call path.
INTERNAL_TOOLS = {"agent_stop", "processing_message", "update_step"}


def _pick_tool(body: dict[str, Any]) -> str | None:
    tools = body.get("tools") or []
    names = [
        tool["function"]["name"] for tool in tools if isinstance(tool, dict) and "function" in tool
    ]
    return next((name for name in names if name not in INTERNAL_TOOLS), None)


def _already_used_tool(body: dict[str, Any]) -> bool:
    return any(m.get("role") == "tool" for m in (body.get("messages") or []))


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 12, "completion_tokens": 64, "total_tokens": 76}


def _completion(message: dict[str, Any], finish: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage(),
    }


def _chunk(content: str, finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish,
            }
        ],
    }


def _tool_call_message(name: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_mock_1",
                "type": "function",
                "function": {"name": name, "arguments": '{"arguments":"mock"}'},
            }
        ],
    }


@app.post("/v1/chat/completions")
async def completions(request: Request) -> Response:
    body = await request.json()
    stream = bool(body.get("stream"))
    delay = float(request.headers.get("x-mock-delay", CHUNK_DELAY))
    target = _pick_tool(body) if not _already_used_tool(body) else None

    # Non-streaming callers (the ReAct tool-selection round) need a JSON body.
    if not stream:
        if target:
            return JSONResponse(_completion(_tool_call_message(target), "tool_calls"))
        return JSONResponse(_completion({"role": "assistant", "content": REPLY}, "stop"))

    async def events() -> AsyncGenerator[bytes, None]:
        if target:
            if NARRATION:
                yield f"data: {json.dumps(_chunk(NARRATION), ensure_ascii=False)}\n\n".encode()
            call = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_mock_1",
                                    "type": "function",
                                    "function": {
                                        "name": target,
                                        "arguments": '{"arguments":"mock"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(call, ensure_ascii=False)}\n\n".encode()
            stop = _chunk("", "tool_calls")
            yield f"data: {json.dumps(stop, ensure_ascii=False)}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        for index in range(0, len(REPLY), 6):
            piece = _chunk(REPLY[index : index + 6])
            yield f"data: {json.dumps(piece, ensure_ascii=False)}\n\n".encode()
            await asyncio.sleep(delay)

        final = _chunk("", "stop")
        final["usage"] = _usage()
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
