/**
 * The audit confirmation dialog.
 *
 * A tool that is about to change something asks here, and the turn is *parked*
 * until this is answered — so this is not a notification, it is a gate. Three
 * rules shape the design:
 *
 * - **The command is editable.** Refusing and re-typing is a bad trade when the
 *   agent was 95% right; the user fixes the path or the flag and approves.
 * - **Refusal carries a reason.** The text goes back to the model as the tool
 *   result, which is what turns a rejection into a course correction instead of
 *   a retry loop.
 * - **Context is read-only.** The previous run's tail is shown in a pseudo
 *   terminal (colours included) because "why does it want to `rm` that" is
 *   usually answered by the last command's output.
 * - **Several can be parked at once.** Parallel tool calls each ask, so the
 *   requests are rendered as a list, one card per call, and each card answers
 *   only its own request — one dialog per call would have to stack modals.
 *
 * Generic on purpose: nothing here knows about shells. Any tool can park a turn
 * through the same broker and get the same dialog.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangleIcon, CheckIcon, SquareTerminalIcon, XIcon } from "lucide-react";

import { AnsiText } from "@/components/ui/ansi-text";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InfoHint } from "@/components/ui/info-hint";
import type { ConfirmRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

export function AuditDialog({
  requests,
  onSubmit,
}: {
  requests: ConfirmRequest[];
  onSubmit: (requestId: string, approved: boolean, command?: string, reason?: string) => void;
}) {
  if (requests.length === 0) return null;
  const many = requests.length > 1;
  return (
    <Dialog open>
      <DialogContent
        showClose={false}
        // The turn cannot proceed without an answer, so closing is not an
        // option: Escape and outside clicks are swallowed on purpose.
        onEscapeKeyDown={(event) => event.preventDefault()}
        onPointerDownOutside={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        className="max-w-2xl gap-3"
        aria-describedby={undefined}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <AlertTriangleIcon className="size-4 text-amber-500" />
            {many ? `${requests.length} 个工具等待确认` : requests[0].title}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {many
              ? "这一轮同时有多个工具在等你确认。它们各自独立，逐个答复即可；全部答完这一轮才会继续。"
              : "这一轮正停在这里等你确认。可以改命令再批准；拒绝时写清理由，理由会作为工具结果返回给模型。"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[70vh] flex-col gap-3 overflow-y-auto pr-1">
          {requests.map((request) => (
            <ConfirmCard key={request.request_id} request={request} onSubmit={onSubmit} />
          ))}
        </div>

        <DialogFooter>
          <span className="text-[11px] text-muted-foreground">
            没有关掉这个框的办法：无人应答时，每一条都会在各自倒计时结束后按拒绝处理。
          </span>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * One parked call.
 *
 * Keyed by ``request_id``, so the draft command and the countdown belong to that
 * request alone: a second prompt arriving while the first is half-edited cannot
 * inherit or overwrite it.
 */
function ConfirmCard({
  request,
  onSubmit,
}: {
  request: ConfirmRequest;
  onSubmit: (requestId: string, approved: boolean, command?: string, reason?: string) => void;
}) {
  const [command, setCommand] = useState(request.command);
  const [reason, setReason] = useState("");
  const [remaining, setRemaining] = useState(() => Math.max(0, Math.round(request.timeout)));
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  // A visible countdown matters: the backend gives up on its own, and an
  // operator who walked back to a closed dialog deserves to know it expired.
  useEffect(() => {
    const timer = window.setInterval(() => {
      setRemaining((prev) => (prev <= 1 ? 0 : prev - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const edited = command.trim() !== request.command.trim();
  const empty = command.trim().length === 0;
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const countdown = minutes > 0 ? `${minutes}:${String(seconds).padStart(2, "0")}` : `${seconds}s`;

  const approve = () => {
    if (empty) return;
    onSubmit(request.request_id, true, command, reason);
  };

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-border bg-muted/30 p-3">
      <header className="flex items-center gap-2 text-sm font-medium">
        <SquareTerminalIcon className="size-4 shrink-0 text-amber-500" />
        <span className="truncate">{request.title}</span>
        <span className="ml-auto shrink-0 font-mono text-[11px] font-normal text-muted-foreground">
          剩余 {countdown}
        </span>
      </header>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
        <span className="rounded bg-muted px-1.5 py-0.5">{request.shell}</span>
        <span className="truncate" title={request.cwd}>
          {request.cwd}
        </span>
        {edited && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">已修改</span>
        )}
        {request.hint && <InfoHint label="这次确认的说明">{request.hint}</InfoHint>}
      </div>

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium">要执行的命令</span>
        <textarea
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          spellCheck={false}
          rows={Math.min(6, Math.max(2, command.split("\n").length))}
          aria-label="要执行的命令"
          className={cn(
            "w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-relaxed outline-none",
            "focus-visible:ring-[3px] focus-visible:ring-ring/40",
            empty && "border-destructive",
          )}
        />
        {empty && (
          <span className="text-[11px] text-destructive">
            命令为空无法执行；想放弃请直接点「拒绝」。
          </span>
        )}
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium">
          拒绝理由
          <span className="ml-1 font-normal text-muted-foreground">（拒绝时填写）</span>
        </span>
        <textarea
          ref={reasonRef}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={2}
          aria-label="拒绝理由"
          placeholder="例如：不要动 node_modules，改成只清理 dist。"
          className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-xs leading-relaxed outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
        />
      </label>

      {request.output && (
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <SquareTerminalIcon className="size-3.5" />
            上一条命令的输出（只读）
          </span>
          <pre className="max-h-32 overflow-auto rounded-md bg-[#0d1117] px-3 py-2 font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
            <AnsiText text={request.output} />
          </pre>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onSubmit(request.request_id, false, command, reason)}
          aria-label="拒绝执行"
        >
          <XIcon />
          拒绝
        </Button>
        <Button size="sm" onClick={approve} disabled={empty} aria-label="批准执行">
          <CheckIcon />
          批准执行
        </Button>
      </div>
    </section>
  );
}
