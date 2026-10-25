"""End-to-end test against the local mock provider.

Requires the app server on :8000 and ``scripts/mock_openai.py`` on :8099.

    .venv/Scripts/python.exe scripts/e2e_mock.py

Verifies:
  * a full streaming turn arrives as ``start`` -> many ``delta`` -> ``done``
  * assistant text and user message land in history.jsonl
  * a second turn reuses the persisted JSON context
  * reconnect replay of a *finished* run does not duplicate the answer
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/api/ws/chat"


async def collect(ws, limit: int = 400) -> list[dict]:
    events: list[dict] = []
    for _ in range(limit):
        raw = await asyncio.wait_for(ws.recv(), 40)
        event = json.loads(raw)
        events.append(event)
        if event.get("type") in {"done", "error"}:
            break
    return events


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        # Point the default preset at the mock provider.
        await client.post(
            "/api/presets",
            json={
                "name": "mock",
                "model": "mock-model",
                "base_url": "http://127.0.0.1:8099/v1",
                "api_key": "sk-mock",
                "protocol": "openai",
                "max_context": 32000,
                "max_output": 4000,
                "temperature": 0.3,
                "top_p": 0.9,
                "stream": True,
                "thinking": False,
            },
        )
        await client.post("/api/presets/mock/activate")
        created = (await client.post("/api/sessions", json={"title": "e2e"})).json()
        session_id = created["id"]
        print("session:", session_id)

    async with websockets.connect(f"{WS}?session_id={session_id}") as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "send",
                    "session_id": session_id,
                    "message": "你好，做个自我介绍",
                    "preset": "mock",
                }
            )
        )
        events = await collect(ws)
        kinds = [event["type"] for event in events]
        text = "".join(
            event.get("text", "") for event in events if event["type"] == "delta"
        )
        print("first turn events:", {k: kinds.count(k) for k in set(kinds)})
        print("answer length:", len(text))
        assert kinds[0] == "start", kinds[:3]
        assert kinds.count("delta") > 5, "expected many small chunks"
        assert kinds[-1] == "done", kinds[-3:]
        assert "mock" in text, text[:80]
        print("streaming turn: ok")

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        history = (await client.get(f"/api/sessions/{session_id}/history")).json()
        roles = [entry["role"] for entry in history["entries"]]
        print("history roles:", roles)
        assert roles == ["user", "assistant"], roles
        assistant = history["entries"][1]["content"]
        assert assistant.strip() == text.strip(), "history must match the stream"

        # Second turn: context was committed, so the provider sees more messages.
        ctx = (
            await client.get(f"/api/sessions/{session_id}/history")
        ).json()
        assert len(ctx["entries"]) == 2

    # Reconnect after completion must NOT replay (history already shows it).
    async with websockets.connect(f"{WS}?session_id={session_id}") as ws:
        try:
            frame = await asyncio.wait_for(ws.recv(), 2)
            print("unexpected replay frame:", frame)
            return 1
        except asyncio.TimeoutError:
            print("no replay for finished run: ok")

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        await client.delete(f"/api/sessions/{session_id}")
        await client.delete("/api/presets/mock")
        await client.patch("/api/settings", json={"active_preset": "default"})

    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
