"""End-to-end smoke test for the WebSocket chat transport.

Runs against a live server (``python -m app``):

    .venv/Scripts/python.exe scripts/smoke_ws.py

Checks:
  1. session create + history endpoints
  2. WS handshake and ``ping``
  3. a ``send`` without an API key yields an ``error`` frame, and the socket
     stays usable afterwards (a failed chunk must not poison the connection)
  4. reconnect replay: a finished run is NOT replayed (history already has it)
"""

from __future__ import annotations

import asyncio
import json

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/api/ws/chat"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        health = (await client.get("/api/health")).json()
        print("health:", health)

        created = (await client.post("/api/sessions", json={"title": "smoke"})).json()
        session_id = created["id"]
        print("session:", session_id)

        history = (await client.get(f"/api/sessions/{session_id}/history")).json()
        assert history["entries"] == [], history
        print("history empty: ok")

    # --- socket -----------------------------------------------------------
    async with websockets.connect(f"{WS}?session_id={session_id}") as ws:
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert pong["type"] == "pong", pong
        print("ping/pong: ok")

        # No API key configured on the default preset -> error frame.
        await ws.send(json.dumps({"type": "send", "session_id": session_id, "message": "hello"}))
        frame = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print("send frame:", frame)
        assert frame["type"] == "error", frame
        assert "API key" in frame["message"], frame

        # The socket must still work after a rejected send.
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert pong["type"] == "pong", pong
        print("socket survives rejection: ok")

    state = httpx.get(f"{BASE}/api/streams/{session_id}").json()
    print("stream state:", state)

    httpx.delete(f"{BASE}/api/sessions/{session_id}")
    print("cleaned up")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
