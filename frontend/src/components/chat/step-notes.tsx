/**
 * A turn's step-by-step narration, folded away from its answer.
 *
 * A step-driven workflow makes the model talk at every step, and each of those
 * talks summarises everything so far — so the same conclusion arrives five
 * times and the real answer is buried in the middle of it. The backend records
 * where each step's narration ends; everything before the last boundary is
 * collected here and collapsed, and only the text after it is rendered as the
 * answer.
 */

import { useState } from "react";
import { ChevronRightIcon } from "lucide-react";

import { Markdown } from "@/components/chat/markdown";
import type { AnswerSection } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface AnswerNote {
  step: string;
  text: string;
}

/**
 * Split a turn's text into the step narrations and the answer.
 *
 * Boundaries with nothing behind them are dropped: a workflow that announces a
 * step *after* the closing sentence would otherwise leave an empty answer and
 * hide the only text the user came for.
 */
export function splitSections(
  content: string,
  sections: AnswerSection[] | undefined,
): { notes: AnswerNote[]; answer: string } {
  const cuts = (sections ?? []).filter(
    (section) => section.end > 0 && section.end <= content.length,
  );
  while (cuts.length > 0 && !content.slice(cuts[cuts.length - 1].end).trim()) {
    cuts.pop();
  }
  if (cuts.length === 0) return { notes: [], answer: content };

  const notes: AnswerNote[] = [];
  let start = 0;
  for (const cut of cuts) {
    const text = content.slice(start, cut.end).trim();
    if (text) notes.push({ step: cut.step, text });
    start = cut.end;
  }
  return { notes, answer: content.slice(start).replace(/^\n+/, "") };
}

const STEP_LABELS: Record<string, string> = {
  decompose: "拆解任务",
  intro: "开始执行",
  leave: "收尾",
};

function stepLabel(step: string): string {
  if (!step) return "准备";
  return STEP_LABELS[step] ?? step;
}

/**
 * The folded narrations, one row per step.
 *
 * Collapsed by default and not a `<details>`: this sits inside a live bubble
 * that re-renders on every chunk, and the browser's own open/closed state does
 * not survive that.
 */
export function StepNotes({
  notes,
  streaming,
}: {
  notes: AnswerNote[];
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (notes.length === 0) return null;

  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-border/70 bg-muted/20">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[11px] text-muted-foreground transition-colors hover:bg-muted/40"
      >
        <ChevronRightIcon
          className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")}
        />
        <span className="font-medium">步骤记录</span>
        <span>
          · {notes.length} 段
          {streaming ? "，仍在进行" : ""}
        </span>
        <span className="ml-auto truncate text-muted-foreground/70">
          {notes.map((note) => stepLabel(note.step)).join(" → ")}
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-border/60 px-2.5 py-2">
          {notes.map((note, index) => (
            <div key={`${index}-${note.step}`} className="flex flex-col gap-1">
              <span className="text-[10px] font-medium tracking-wide text-muted-foreground/80 uppercase">
                {stepLabel(note.step)}
              </span>
              <Markdown
                content={note.text}
                className="text-[0.85rem] leading-6 text-muted-foreground"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
