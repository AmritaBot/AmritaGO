"""Reconnect-replay test: drop the socket mid-answer, then reattach.

Requires the app on :8000 and the mock provider on :8099.

    .venv/Scripts/python.exe scripts/check_replay.py

The core guarantee under test: a client that disconnects while the model is
still streaming must, after reconnecting, receive the *whole* answer (the
server replays its buffer) and must not receive it twice.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/api/ws/chat"


async def read_until_done(ws, *, stop_after: int | None = None) -> tuple[list[dict], bool]:
    events: list[dict] = []
    while True:
        if stop_after is not None and len(events) >= stop_after:
            return events, False
        try:
            raw = await asyncio.wait_for(ws.recv(), 30)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return events, False
        event = json.loads(raw)
        events.append(event)
        if event.get("type") in {"done", "error"}:
            return events, True


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
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
        session_id = (
            await client.post("/api/sessions", json={"title": "replay"})
        ).json()["id"]
    print("session:", session_id)

    # --- first connection: abandon it mid-stream --------------------------
    async with websockets.connect(f"{WS}?session_id={session_id}") as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "send",
                    "session_id": session_id,
                    "message": "请流式输出一段长文本",
                    "preset": "mock",
                }
            )
        )
        partial, finished = await read_until_done(ws, stop_after=4)
        print("abandoned after", len(partial), "events; finished =", finished)
        if finished:
            for event in partial:
                print("  early event:", json.dumps(event, ensure_ascii=False)[:200])
        assert not finished

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        state = (await client.get(f"/api/streams/{session_id}")).json()
        print("stream state after disconnect:", state)
        assert state["active"] is True, "the run must survive a dropped socket"

    # --- reconnect: the whole answer must replay from byte 0 --------------
    await asyncio.sleep(0.4)
    async with websockets.connect(f"{WS}?session_id={session_id}") as ws:
        events, finished = await read_until_done(ws)
        kinds = [event["type"] for event in events]
        text = "".join(
            event.get("text", "") for event in events if event["type"] == "delta"
        )
        print("replay events:", {k: kinds.count(k) for k in set(kinds)})
        print("replayed chars:", len(text))
        assert kinds.count("start") == 1, "exactly one start marker on replay"
        assert finished, "replay must end with done"
        assert len(text) > 100, "the full answer must be replayed"

    # History must contain the answer exactly once.
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as client:
        history = (await client.get(f"/api/sessions/{session_id}/history")).json()
        roles = [entry["role"] for entry in history["entries"]]
        print("history roles:", roles)
        assert roles.count("assistant") == 1, roles
        assert history["entries"][-1]["content"].strip() == text.strip()

        await client.delete(f"/api/sessions/{session_id}")
        await client.delete("/api/presets/mock")
        await client.patch("/api/settings", json={"active_preset": "default"})

    print("REPLAY OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
