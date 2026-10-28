"""End-to-end check for stop-and-queue behaviour.

Needs a live server (``python -m app``) and the mock provider:

    uv run --no-sync python scripts/mock_openai.py --port 8099
    uv run --no-sync python scripts/e2e_inject.py

What it asserts:
  1. ``react-step`` advertises a graceful suspend point; ``react`` does not.
  2. ``prompt_offer`` queues a message and ends the current turn at that point.
  3. The queued message becomes the next turn on the *same* socket.
  4. ``prompt_revoke`` withdraws a queued message without ending the turn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

    def types(self) -> list[str]:
        return [str(event.get("type")) for event in self.items]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8013")
    parser.add_argument("--workflow", default="react-step")
    args = parser.parse_args()
    ws_url = args.base.replace("http", "ws") + "/api/ws/chat"

    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        # Point a provider at the mock so a turn can actually run.
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
        await client.post("/api/workflows/activate", json={"name": args.workflow})
        listing = (await client.get("/api/workflows")).json()
        specs = {item["name"]: item for item in listing["workflows"]}
        step = specs["react-step"]["inject"]
        plain = specs["react"]["inject"]
        assert step["graceful"] is True, step
        assert step["points"], step
        assert plain["graceful"] is False, plain
        expected = specs[args.workflow]["inject"]
        print("capabilities ok")

        created = (await client.post("/api/sessions", json={"title": "inject"})).json()
        session_id = created["id"]

    events = Events()

    async with websockets.connect(ws_url) as socket:

        async def pump() -> None:
            async for raw in socket:
                events.push(json.loads(raw))

        pump_task = asyncio.create_task(pump())
        try:
            # 1. Start a turn.
            await socket.send(
                json.dumps(
                    {
                        "type": "send",
                        "session_id": session_id,
                        "message": "第一轮：请说明项目结构",
                    }
                )
            )
            start = await events.until(lambda e: e["type"] == "start")
            assert start["inject"]["graceful"] == expected["graceful"], start["inject"]
            print("start ok:", start["workflow"], start["inject"]["points"])

            # 2. Queue a second message; the offer is acknowledged first.
            await socket.send(
                json.dumps(
                    {
                        "type": "prompt_offer",
                        "session_id": session_id,
                        "message": "第二轮：换个话题",
                        "mode": "graceful",
                        "stop": True,
                    }
                )
            )
            queued = await events.until(lambda e: e["type"] == "queued", timeout=30)
            assert queued["handle"]["content"] == "第二轮：换个话题", queued
            print("queued ok:", queued["handle"]["id"])

            # Verify the declared suspend point is reached instead of silently falling back to immediate stop.
            ack = await events.until(lambda e: e["type"] == "stop_ack", timeout=40)
            assert ack["accepted"] is True, ack
            if expected["graceful"]:
                assert ack["parked"] is True, ack
                assert ack["mode"] == "graceful", ack
                print("parked ok:", ack["latency"], "s")
            else:
                assert ack["mode"] == "immediate", ack
                print("immediate ok:", ack["reason"])

            # 3. Verify the stopped turn chains to the next turn on the same socket without a gap.
            first_end = await events.until(lambda e: e["type"] == "turn_end")
            assert first_end["stopped"] is True, first_end
            second_start = await events.until(lambda e: e["type"] == "start" and e is not start)
            assert second_start["stream_id"] != start["stream_id"]
            print("chained ok:", first_end["stopped"], second_start["stream_id"][:8])

            done = await events.until(lambda e: e["type"] == "done")
            print("done ok:", done["workflow"])

            # 4. Verify revoking a queued message does not stop the running turn.
            events.clear()
            await socket.send(
                json.dumps(
                    {
                        "type": "send",
                        "session_id": session_id,
                        "message": "第三轮：这条会继续跑完",
                    }
                )
            )
            await events.until(lambda e: e["type"] == "start")
            await socket.send(
                json.dumps(
                    {
                        "type": "prompt_offer",
                        "session_id": session_id,
                        "message": "不该被执行",
                        "mode": "graceful",
                        "stop": False,
                    }
                )
            )
            handle = (await events.until(lambda e: e["type"] == "queued"))["handle"]
            await socket.send(
                json.dumps(
                    {
                        "type": "prompt_revoke",
                        "session_id": session_id,
                        "handle_id": handle["id"],
                    }
                )
            )
            unqueued = await events.until(lambda e: e["type"] == "unqueued")
            assert unqueued["status"] == "revoked", unqueued
            print("revoke ok:", unqueued["status"])

            # The turn was never stopped, so exactly one turn_end arrives.
            await events.until(lambda e: e["type"] == "done")
            ends = [e for e in events.items if e["type"] == "turn_end"]
            assert len(ends) == 1, events.types()
            assert ends[0]["stopped"] is False, ends[0]
            print("revoke kept the turn running ok")
        finally:
            pump_task.cancel()

    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        history = (await client.get(f"/api/sessions/{session_id}/history")).json()
        users = [e["content"] for e in history["entries"] if e["role"] == "user"]
        assert "不该被执行" not in users, users
        assert any("第二轮" in text for text in users), users
        print("history ok:", len(history["entries"]), "entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
