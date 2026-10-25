/**
 * A Markdown body that can also be read as its own source.
 *
 * Two audiences, one field: someone reading a system prompt wants to see how it
 * renders (does the list break, is the heading level right), and someone
 * debugging a preset wants the exact characters. A split that guessed between
 * them would be wrong for one of them, so it is an explicit toggle.
 *
 * The default is rendered — that is what the prompt *is* to the model's reader —
 * and the source view is monospace and selectable so it can be copied verbatim.
 */

import { useState } from "react";
import { CodeIcon, EyeIcon } from "lucide-react";

import { Markdown } from "@/components/chat/markdown";
import { cn } from "@/lib/utils";

export function MarkdownOrSource({
  content,
  emptyHint = "（空）",
  className,
  sourceHeightClass = "max-h-72",
}: {
  content: string;
  emptyHint?: string;
  className?: string;
  /** Height cap for the source view; the rendered view grows freely. */
  sourceHeightClass?: string;
}) {
  const [mode, setMode] = useState<"rendered" | "source">("rendered");

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-center gap-1 rounded-md border border-border bg-muted/30 p-0.5 self-start">
        <button
          type="button"
          onClick={() => setMode("rendered")}
          aria-pressed={mode === "rendered"}
          title="渲染后长什么样"
          className={cn(
            "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
            mode === "rendered"
              ? "bg-background text-foreground shadow-xs"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <EyeIcon className="size-3" />
          渲染
        </button>
        <button
          type="button"
          onClick={() => setMode("source")}
          aria-pressed={mode === "source"}
          title="原始 Markdown 源码（可选中复制）"
          className={cn(
            "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition-colors",
            mode === "source"
              ? "bg-background text-foreground shadow-xs"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <CodeIcon className="size-3" />
          源码
        </button>
      </div>

      {mode === "rendered" ? (
        <div className="rounded-md border border-border bg-muted/20 px-3 py-2 text-sm">
          {content.trim() ? <Markdown content={content} /> : (
            <span className="text-muted-foreground">{emptyHint}</span>
          )}
        </div>
      ) : (
        <pre
          className={cn(
            "overflow-auto rounded-md border border-border bg-muted/20 p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap select-text",
            sourceHeightClass,
          )}
        >
          {content.trim() ? content : emptyHint}
        </pre>
      )}
    </div>
  );
}
