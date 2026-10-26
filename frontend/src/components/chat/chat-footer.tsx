/**
 * Footer row under the composer: the run's numbers plus the session actions.
 *
 * One row, deliberately. This used to be three stacked blocks (a "重新载入"
 * row, a stats row, a connection row) which added ~40px of dead height under
 * the input box for information that is all of one line's worth. The reload
 * button moves into that same row, at the far right where the eye already
 * looks for a session-level action.
 *
 * The terminal lives here too: shell sessions are part of *this* conversation
 * (the agent ran those commands for this task), so inspecting them belongs on
 * the main screen, not three levels into a settings dialog.
 */

import { useState } from "react";
import { ChevronDownIcon, SquareTerminalIcon } from "lucide-react";

import { RunMeta } from "@/components/chat/run-meta";
import { TerminalPanel } from "@/components/chat/terminal-panel";
import type { LiveUsage, RunPlan } from "@/hooks/use-chat-socket";
import type { TurnUsage, UsageTotals } from "@/lib/api";
import { cn } from "@/lib/utils";

export function ChatFooter({
  busy,
  plan,
  live,
  last,
  totals,
  connection,
  onReload,
  contextMessages,
}: {
  busy: boolean;
  plan: RunPlan | null;
  live: LiveUsage | null;
  last: TurnUsage | null;
  totals: UsageTotals | null;
  connection: "connecting" | "open" | "closed";
  onReload: () => void;
  contextMessages?: number | null;
}) {
  const [terminalOpen, setTerminalOpen] = useState(false);

  return (
    <div className="shrink-0 border-t border-border/60 bg-background">
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2 px-4 py-1">
        <button
          type="button"
          onClick={() => setTerminalOpen((prev) => !prev)}
          aria-expanded={terminalOpen}
          title="查看 Agent 在本会话里用过的 shell 与命令输出"
          className={cn(
            "inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[11px] transition-colors hover:bg-muted",
            terminalOpen ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <SquareTerminalIcon className="size-3" />
          终端
          <ChevronDownIcon
            className={cn(
              "size-3 transition-transform",
              terminalOpen && "rotate-180",
            )}
          />
        </button>

        <RunMeta
          busy={busy}
          plan={plan}
          live={live}
          last={last}
          totals={totals}
          connection={connection}
          onReload={onReload}
          contextMessages={contextMessages}
        />
      </div>

      {terminalOpen && (
        <div className="mx-auto max-h-[45vh] w-full max-w-3xl overflow-y-auto px-4 pb-3">
          <TerminalPanel />
        </div>
      )}
    </div>
  );
}
