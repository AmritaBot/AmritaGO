"""End-to-end check for editing the last prompt while a turn is running.

Needs a live server (``python -m app``) and the mock provider:

    uv run --no-sync python scripts/mock_openai.py --port 8099
    uv run --no-sync python scripts/e2e_edit.py

What it asserts:
  1. Editing the running prompt ends the turn and rewinds the transcript.
  2. The rewritten prompt can be sent again and is the only thing kept.
  3. A prompt queued behind the turn is vetoed by the edit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import suppress
from typing import Any

import httpx
import websockets


class Events:
    """Ordered event log from one socket, with awaitable predicates."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def push(self, event: dict[str, Any]) -> None:
        self.items.append(event)
        self._changed.set()

    async def until(self, predicate, timeout: float = 120.0) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for event in self.items:
                if predicate(event):
                    return event
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"event not seen; got {[e['type'] for e in self.items]}")
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"event not seen; got {[e['type'] for e in self.items]}"
                ) from None

    def clear(self) -> None:
        self.items.clear()
        self._changed.clear()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8013")
    args = parser.parse_args()
    ws_url = args.base.replace("http", "ws") + "/api/ws/chat"

    async with httpx.AsyncClient(base_url=args.base, timeout=120) as client:
        provider = (
            await client.post(
                "/api/providers",
                json={
                    "name": "mock",
                    "base_url": "http://127.0.0.1:8099/v1",
                    "api_key": "sk-mock",
                    "protocol": "openai",
                    "models": [{"id": "mock-model"}],
                },
            )
        ).json()
        provider_id = provider.get("id") or provider.get("provider", {}).get("id")
        await client.post("/api/models/activate", json={"key": f"{provider_id}::mock-model"})
        await client.post("/api/workflows/activate", json={"name": "react-step"})
        created = (await client.post("/api/sessions", json={"title": "edit"})).json()
        session_id = created["id"]

    events = Events()

    async def history(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        listing = await client.get(f"/api/sessions/{session_id}/history")
        return listing.json()["entries"]

    async with httpx.AsyncClient(base_url=args.base, timeout=120) as client:
        async with websockets.connect(ws_url) as socket:

            async def pump() -> None:
                async for raw in socket:
                    events.push(json.loads(raw))

            pump_task = asyncio.create_task(pump())
            try:
                # 1. Edit the prompt of a turn that is still running.
                events.clear()
                await socket.send(
                    json.dumps({"type": "send", "session_id": session_id, "message": "原始问题"})
                )
                await events.until(lambda e: e["type"] == "start")
                edited = (
                    await client.post(
                        f"/api/sessions/{session_id}/messages/edit",
                        json={"content": "改过的问题", "expected": "原始问题"},
                    )
                ).json()
                assert edited["ok"] is True, edited
                assert edited["dropped_prompt"] is False, edited
                await events.until(lambda e: e["type"] == "done")
                print("edit while running ok")

                # The running turn was cut off, so nothing of it survives.
                entries = await history(client)
                assert entries == [], entries
                print("rollback ok")

                # 2. The rewritten prompt runs as an ordinary turn.
                events.clear()
                await socket.send(
                    json.dumps({"type": "send", "session_id": session_id, "message": "改过的问题"})
                )
                await events.until(lambda e: e["type"] == "done")
                entries = await history(client)
                assert [entry["content"] for entry in entries if entry["role"] == "user"] == [
                    "改过的问题"
                ], entries
                print("resend ok")

                # 3. A queued prompt is vetoed by the edit.
                events.clear()
                await socket.send(
                    json.dumps(
                        {"type": "send", "session_id": session_id, "message": "第三轮：要改的"}
                    )
                )
                await events.until(lambda e: e["type"] == "start")
                await socket.send(
                    json.dumps(
                        {
                            "type": "prompt_offer",
                            "session_id": session_id,
                            "message": "排队消息",
                            "stop": False,
                        }
                    )
                )
                await events.until(lambda e: e["type"] == "queued")
                vetoed = (
                    await client.post(
                        f"/api/sessions/{session_id}/messages/edit",
                        json={"content": "第三轮：改过了", "expected": "第三轮：要改的"},
                    )
                ).json()
                assert vetoed["dropped_prompt"] is True, vetoed
                await events.until(lambda e: e["type"] == "done")
                state = (await client.get(f"/api/streams/{session_id}")).json()
                assert state["pending"] is None, state
                entries = await history(client)
                contents = [entry["content"] for entry in entries]
                assert "排队消息" not in contents, entries
                assert "第三轮：要改的" not in contents, entries
                prompts = [entry["content"] for entry in entries if entry["role"] == "user"]
                assert prompts == ["改过的问题"], entries
                print("veto ok")
            finally:
                pump_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pump_task
    print("e2e_edit ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
