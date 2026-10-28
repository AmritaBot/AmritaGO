"""Self-check for the prompt-injection layer.

Run: ``uv run --no-sync python scripts/check_inject.py``

Covers the parts that are pure logic — capability declaration, registration-time
validation, and the single-slot semantics including the revoke races. The live
stop path needs a model and is exercised by ``scripts/e2e_inject.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-inject-"))

from app.runtime.prompt_inject import (
    PromptSlot,
    QueuedPrompt,
    RevokeStatus,
    StopMode,
)
from app.runtime.workflows import (
    BUILTIN_WORKFLOWS,
    STEP_SUSPEND_POINT,
    WorkflowError,
    WorkflowSpec,
    validate_inject,
)


def check_capabilities() -> None:
    step = BUILTIN_WORKFLOWS["react-step"]
    assert step.inject.graceful, "react-step must declare a suspend point"
    assert step.inject.graceful_tags == (STEP_SUSPEND_POINT,), step.inject.graceful_tags
    assert step.inject.to_public()["graceful"] is True

    for name in ("react", "chat"):
        spec = BUILTIN_WORKFLOWS[name]
        assert not spec.inject.graceful, f"{name} must not claim a suspend point"
        public = spec.inject.to_public()
        assert public["graceful"] is False
        assert public["immediate"] is True, "every workflow can be stopped immediately"
    print("capabilities ok")


def check_validation() -> None:
    bad = WorkflowSpec(
        name="broken",
        title="broken",
        description="",
        strategy=BUILTIN_WORKFLOWS["react-step"].strategy,
        pipeline=BUILTIN_WORKFLOWS["react-step"].pipeline,
        inject=BUILTIN_WORKFLOWS["react-step"].inject.__class__(
            points={  # type: ignore[arg-type]
                "ChatObject::no_such_node": frozenset({StopMode.GRACEFUL})
            }
        ),
    )
    try:
        validate_inject(bad)
    except WorkflowError as exc:
        assert "no_such_node" in str(exc), exc
    else:
        raise AssertionError("a typo in a suspend point must fail at registration")
    print("validation ok")


async def check_slot() -> None:
    slot = PromptSlot()

    first = await slot.offer("s1", QueuedPrompt(content="一"))
    assert first.replaced is None

    # Replacing an occupied slot returns its old handle so the caller can restore that text.
    second = await slot.offer("s1", QueuedPrompt(content="二"))
    assert second.replaced is not None and second.replaced.id == first.handle.id

    # The replaced handle is gone: revoking it is a miss, not a success.
    assert await slot.revoke(first.handle.id) is RevokeStatus.NOT_FOUND
    assert await slot.peek() is not None

    taken = await slot.take()
    assert taken is not None and taken.id == second.handle.id
    assert await slot.peek() is None

    # A taken prompt already belongs to the next turn and can only be changed by message editing.
    assert await slot.revoke(second.handle.id) is RevokeStatus.TOO_LATE
    assert await slot.take() is None

    third = await slot.offer("s1", QueuedPrompt(content="三"))
    assert await slot.revoke(third.handle.id) is RevokeStatus.REVOKED
    assert await slot.peek() is None

    # Revoke is idempotent.
    assert await slot.revoke(third.handle.id) is RevokeStatus.NOT_FOUND
    print("slot ok")


async def main() -> None:
    check_capabilities()
    check_validation()
    await check_slot()


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
