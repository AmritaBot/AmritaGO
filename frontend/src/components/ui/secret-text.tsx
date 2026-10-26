/**
 * Renders a string with credential-looking tokens masked, revealing the
 * original on hover or keyboard focus.
 *
 * The revealed value is a real DOM node, so a determined reader can still get
 * it — that is the point. This is about not leaving keys on screen when you
 * screenshot a conversation, not about hiding them from the person who typed
 * them.
 */

import { useMemo } from "react";

import { maskSecret, splitSecrets } from "@/lib/secrets";
import { cn } from "@/lib/utils";

function SecretToken({ value }: { value: string }) {
  const masked = maskSecret(value);
  return (
    // Use tabIndex so the inline control remains valid inside links.
    <span className="group/secret relative inline">
      <span
        tabIndex={0}
        title={value}
        className={cn(
          "cursor-help rounded-[3px] bg-current/15 px-1 font-mono text-[0.9em] underline decoration-dotted underline-offset-2",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current/40",
        )}
      >
        {masked}
      </span>
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute top-full left-0 z-30 mt-1 hidden w-max max-w-[min(80vw,32rem)]",
          "rounded-md border border-border bg-popover px-2 py-1 font-mono text-[11px] leading-relaxed break-all",
          "text-popover-foreground shadow-md",
          "group-hover/secret:block group-focus-within/secret:block",
        )}
      >
        {value}
      </span>
    </span>
  );
}

export function SecretText({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  const parts = useMemo(() => splitSecrets(text), [text]);
  if (parts.length === 1 && typeof parts[0] === "string") {
    return <>{text}</>;
  }
  return (
    <span className={className}>
      {parts.map((part, index) =>
        typeof part === "string" ? (
          part
        ) : (
          <SecretToken
            key={`${index}-${part.secret.slice(-4)}`}
            value={part.secret}
          />
        ),
      )}
    </span>
  );
}
