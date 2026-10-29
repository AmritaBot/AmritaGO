"""Self-check for step-narration folding.

Run: ``uv run --no-sync python scripts/check_sections.py``

A step-driven workflow makes the model narrate at every step, and each of those
narrations summarises the whole task. The boundaries are recorded here so the UI
can fold everything before the last one; this checks the offsets, and the two
degradations that matter — one narration (nothing to fold) and a workflow that
announces a step after the closing sentence (nothing may be hidden).
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("AMRITA_GO_DATA", tempfile.mkdtemp(prefix="amrita-sections-"))

from app.runtime.agent_runtime import TurnDraft


def split(content: str, sections: list[dict]) -> tuple[list[dict], str]:
    """Mirror of the frontend's ``splitSections``."""
    cuts = [item for item in sections if 0 < item["end"] <= len(content)]
    while cuts and not content[cuts[-1]["end"] :].strip():
        cuts.pop()
    if not cuts:
        return [], content
    notes: list[dict] = []
    start = 0
    for cut in cuts:
        text = content[start : cut["end"]].strip()
        if text:
            notes.append({"step": cut["step"], "text": text})
        start = cut["end"]
    return notes, content[start:].lstrip("\n")


def build(blocks: list[str], boundaries: list[int]) -> TurnDraft:
    """Feed ``blocks`` and close a section at each index in ``boundaries``."""
    draft = TurnDraft(session_id="check")
    for index, block in enumerate(blocks):
        draft.answer.append(block)
        if index in boundaries:
            draft.close_section(f"step{index}")
    draft.settle_sections()
    return draft


def check_multi_step() -> None:
    draft = build(["第一段总结。", "第二段总结。", "结论。"], [0, 1])
    assert draft.sections == [
        {"step": "", "end": len("第一段总结。")},
        {"step": "step0", "end": len("第一段总结。第二段总结。")},
    ], draft.sections
    notes, answer = split(draft.answer_text, draft.sections)
    assert [note["text"] for note in notes] == ["第一段总结。", "第二段总结。"], notes
    assert answer == "结论。", answer
    print("multi-step ok")


def check_single_block() -> None:
    """One narration and no answer after it: nothing may be folded away."""
    draft = build(["唯一一段。"], [0])
    assert draft.sections == [], draft.sections
    notes, answer = split(draft.answer_text, draft.sections)
    assert notes == [] and answer == "唯一一段。", (notes, answer)
    print("single-block ok")


def check_empty_steps() -> None:
    """Steps that announce themselves before saying anything add no rows."""
    draft = TurnDraft(session_id="check")
    draft.close_section("decompose")
    draft.close_section("intro")
    draft.answer.append("正文。")
    draft.close_section("leave")
    draft.answer.append("结论。")
    draft.settle_sections()
    assert draft.sections == [{"step": "intro", "end": len("正文。")}], draft.sections
    notes, answer = split(draft.answer_text, draft.sections)
    assert [note["step"] for note in notes] == ["intro"], notes
    assert answer == "结论。", answer
    print("empty-steps ok")


def check_trailing_whitespace() -> None:
    """A boundary followed only by blank text still yields an answer."""
    draft = build(["总结。", "结论。\n\n"], [0, 1])
    notes, answer = split(draft.answer_text, draft.sections)
    assert answer == "结论。\n\n", repr(answer)
    assert [note["text"] for note in notes] == ["总结。"], notes
    print("trailing-whitespace ok")


def main() -> None:
    check_multi_step()
    check_single_block()
    check_empty_steps()
    check_trailing_whitespace()
    print("sections ok")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
