from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from app.runtime import events


async def main() -> None:
    calls: list[tuple[str, Any]] = []

    def sync_handler(event: events.PluginEvent) -> None:
        calls.append(("sync", (event.session_id, event["content"])))

    async def field_handler(session_id: str, content: str) -> None:
        calls.append(("field", (session_id, content)))

    first = events.subscribe("demo", sync_handler)
    second = events.subscribe("demo", field_handler, priority=20)

    await events.emit("demo", session_id="s1", content="hi")
    assert calls == [("sync", ("s1", "hi")), ("field", ("s1", "hi"))], calls

    # Mapping access still works for handlers written against the old hook API.
    seen: list[dict[str, Any]] = []

    def mapping_handler(event: Mapping[str, Any]) -> None:
        seen.append(dict(event))

    third = events.subscribe("demo", mapping_handler, priority=30)
    await events.emit("demo", session_id="s2", content="again")
    assert seen[-1] == {"session_id": "s2", "content": "again"}, seen

    # Blocking stops the handlers behind it.
    calls.clear()
    blocker = events.subscribe("demo", sync_handler, priority=1, block=True)
    await events.emit("demo", session_id="s3", content="blocked")
    assert calls == [("sync", ("s3", "blocked"))], calls
    blocker.close()

    # Unsubscribing really removes the handler from AmritaSense.
    calls.clear()
    first.close()
    second.close()
    third.close()
    await events.emit("demo", session_id="s4", content="gone")
    assert calls == [], calls

    from amrita_sense.hook.matcher import EventRegistry

    assert not EventRegistry().get_handlers("demo"), EventRegistry().get_all()["demo"]

    # A failing handler must not fail the emitter.
    broken = events.subscribe("demo", lambda event: 1 / 0)
    await events.emit("demo", session_id="s5")
    broken.close()

    # The plugin runtime removes its handlers when the plugin unloads.
    from amrita_sense.hook.matcher import EventRegistry

    from app.plugins import PluginRuntime

    runtime = PluginRuntime()
    hits: list[str] = []

    async def demo_plugin(ctx: Any):
        async def on_demo(event: events.PluginEvent) -> None:
            hits.append(str(event.session_id))

        ctx.on("demo_event", on_demo)
        yield

    runtime.register(demo_plugin, name="demo")
    await runtime.activate()
    assert EventRegistry().get_handlers("demo_event"), "handler not registered"
    await events.emit("demo_event", session_id="plugin")
    assert hits == ["plugin"], hits
    await runtime.deactivate()
    assert not EventRegistry().get_handlers("demo_event"), "handler survived unload"
    await events.emit("demo_event", session_id="after")
    assert hits == ["plugin"], hits
    print("events ok")


asyncio.run(main())
