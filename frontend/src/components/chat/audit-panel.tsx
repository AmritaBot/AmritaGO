/**
 * Tool approvals, inline above the composer.
 *
 * A tool that is about to change something asks here, and the turn is *parked*
 * until every request is answered. Four rules shape the design:
 *
 * - **Not a modal.** The conversation stays readable while the question is open:
 *   deciding whether `rm -rf dist` is fine usually means re-reading what the
 *   agent said a moment ago, and a modal that covers the transcript forces the
 *   user to answer from memory.
 * - **The command is editable.** Refusing and re-typing is a bad trade when the
 *   agent was 95% right; the user fixes the path or the flag and approves.
 * - **Refusal carries a reason.** The text goes back to the model as the tool
 *   result, which is what turns a rejection into a course correction instead of
 *   a retry loop.
 * - **Several can be parked at once.** Parallel tool calls each ask, so requests
 *   render as a list, one card per call, and each card answers only its own.
 *
 * There is no countdown: approval has no deadline, so a prompt stays until it is
 * answered or the turn is stopped.
 */

import { useState } from "react";
import { AlertTriangleIcon, CheckIcon, SquareTerminalIcon, XIcon } from "lucide-react";

import { AnsiText } from "@/components/ui/ansi-text";
import { Button } from "@/components/ui/button";
import { InfoHint } from "@/components/ui/info-hint";
import type { ConfirmRequest } from "@/lib/api";
import { cn } from "@/lib/utils";

export function AuditPanel({
  requests,
  onSubmit,
}: {
  requests: ConfirmRequest[];
  onSubmit: (requestId: string, approved: boolean, command?: string, reason?: string) => void;
}) {
  if (requests.length === 0) return null;
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-1">
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 shadow-sm">
        <div className="flex items-center gap-2 border-b border-amber-500/25 px-4 py-2">
          <AlertTriangleIcon className="size-4 shrink-0 text-amber-500" />
          <span className="text-sm font-medium">
            {requests.length > 1
              ? `${requests.length} 个工具等待确认`
              : "Agent 想执行命令"}
          </span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {requests.length > 1
              ? "逐个答复，全部答完这一轮才继续"
              : "这一轮停在这里等你确认"}
          </span>
        </div>
        <div className="flex max-h-[46vh] flex-col gap-3 overflow-y-auto p-3">
          {requests.map((request) => (
            <ConfirmCard key={request.request_id} request={request} onSubmit={onSubmit} />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * One parked call.
 *
 * Keyed by ``request_id``, so the draft command and the reason belong to that
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

  const edited = command.trim() !== request.command.trim();
  const empty = command.trim().length === 0;

  const approve = () => {
    if (empty) return;
    onSubmit(request.request_id, true, command, reason);
  };

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
        <SquareTerminalIcon className="size-3.5 shrink-0 text-amber-500" />
        <span className="rounded bg-muted px-1.5 py-0.5">{request.shell}</span>
        <span className="truncate" title={request.cwd}>
          {request.cwd}
        </span>
        {edited && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">已修改</span>
        )}
        {request.hint && <InfoHint label="这次确认的说明">{request.hint}</InfoHint>}
      </div>

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

      <textarea
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        rows={2}
        aria-label="拒绝理由"
        placeholder="拒绝理由（拒绝时填写）：例如不要动 node_modules，改成只清理 dist。"
        className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-xs leading-relaxed outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
      />

      {request.output && (
        <details className="group">
          <summary className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground select-none">
            <SquareTerminalIcon className="size-3.5" />
            上一条命令的输出（只读）
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-[#0d1117] px-3 py-2 font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
            <AnsiText text={request.output} />
          </pre>
        </details>
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
