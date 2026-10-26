/**
 * A single "?" that reveals the details on demand.
 *
 * Screens were accumulating paragraphs of explanation above the thing they
 * explained, which pushed the actual controls off the fold. The rule now is:
 * the interface says what a control does in one line, and anything longer lives
 * behind this button.
 *
 * It is a plain absolutely-positioned panel rather than a Radix popover,
 * because it is always used inline inside text blocks and only needs to close
 * on outside click / Escape.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { HelpCircleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function InfoHint({
  children,
  label = "说明",
  className,
  align = "start",
  side = "bottom",
  size = "sm",
}: {
  children: ReactNode;
  /** Accessible name; also the tooltip on the button. */
  label?: string;
  className?: string;
  align?: "start" | "end";
  /** ``top`` for a hint sitting at the bottom of the window (the run strip). */
  side?: "bottom" | "top";
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span
      ref={root}
      className={cn("relative inline-flex align-middle", className)}
    >
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        title={label}
        onClick={() => setOpen((prev) => !prev)}
        className={cn(
          "inline-flex items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
          size === "md" ? "size-6" : "size-5",
          open && "bg-accent text-foreground",
        )}
      >
        <HelpCircleIcon className={size === "md" ? "size-4" : "size-3.5"} />
      </button>
      {open && (
        <span
          id={id}
          role="note"
          className={cn(
            "absolute z-50 block w-80 max-w-[min(20rem,calc(100vw-2rem))] rounded-lg border border-border bg-popover p-3 text-left text-xs leading-relaxed font-normal whitespace-normal text-popover-foreground shadow-lg",
            side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
