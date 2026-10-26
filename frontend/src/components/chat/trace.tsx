/**
 * The assistant turn's timeline.
 *
 * A turn is not "some thinking, then some tools, then an answer": thinking and
 * tool calls alternate as the model works, and their order is the only thing
 * that explains the result. So they render in arrival order, one row per step,
 * with the answer below the list.
 *
 * Pairing a call with its completion is done here rather than in the hook: the
 * start and finish events sit at different points in the timeline, and folding
 * them at render time keeps the earlier position (that is when the call began).
 */

import { useState } from "react";
import {
  BanIcon,
  BrainIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleAlertIcon,
  CircleSlashIcon,
  CopyIcon,
  HelpCircleIcon,
  InfoIcon,
  Loader2Icon,
  TimerIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { ChatMessage } from "@/hooks/use-chat-socket";
import type { RunningTool } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

type Entry = ChatMessage["trace"][number];
type MetaEvent = ChatMessage["events"][number];

interface ToolCall {
  key: string;
  name: string;
  /**
   * ``unknown`` is not ``failed``: the run ended without a completion event, so
   * calling that a failure would invent an outcome. ``cancelled`` and
   * ``timeout`` are the two ways a call can end without a result of its own.
   */
  status: "running" | "done" | "failed" | "unknown" | "cancelled" | "timeout";
  args: string | null;
  result: string | null;
  error: string | null;
  finished: boolean;
}

type Item =
  | { kind: "reasoning"; key: string; text: string }
  | { kind: "tool"; key: string; call: ToolCall }
  | {
      kind: "notice";
      key: string;
      content: string;
      metadata: Record<string, unknown>;
    };

/** Is this event part of a tool call (rather than a step or a notice)? */
function toolNameOf(event: {
  metadata: Record<string, unknown>;
}): string | null {
  const kind = String(event.metadata.type ?? "");
  if (kind === "function_call" || kind === "tool_call") {
    return String(event.metadata.function_name ?? "") || "tool";
  }
  // Treat skill_call as a tool row only when it has its own tool_id; otherwise it is a notice.
  if (kind === "skill_call" && event.metadata.tool_id) {
    return String(event.metadata.skill_name ?? "") || "skill";
  }
  return null;
}

/** Human-readable arguments: ``key=value`` pairs rather than raw JSON. */
function describeArgs(event: MetaEvent): string | null {
  const raw = event.metadata.arguments;
  if (raw == null) return null;
  const text = typeof raw === "string" ? raw : JSON.stringify(raw);
  if (!text) return null;
  try {
    const parsed = JSON.parse(text) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const pairs = Object.entries(parsed as Record<string, unknown>).map(
        ([key, value]) =>
          `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`,
      );
      if (pairs.length > 0) return pairs.join(", ");
    }
  } catch {
    // Not JSON (a bare command, say): the text itself is the description.
  }
  return text;
}

function foldCall(call: ToolCall, event: MetaEvent): ToolCall {
  const done = event.metadata.is_done === true;
  const error = event.metadata.err == null ? null : String(event.metadata.err);
  const result =
    typeof event.metadata.result === "string" ? event.metadata.result : null;
  // Keep cancelled and timed-out calls from being relabeled as completed.
  const settled = call.status === "cancelled" || call.status === "timeout";
  return {
    ...call,
    finished: call.finished || done,
    error: call.error ?? error,
    result: result ?? call.result,
    args: call.args ?? describeArgs(event),
    status: settled
      ? call.status
      : call.finished || done
        ? (call.error ?? error)
          ? "failed"
          : "done"
        : "running",
  };
}

/** Notices that say a call was cut short, and which call they belong to. */
function interruptedStatus(
  metadata: Record<string, unknown>,
): ToolCall["status"] | null {
  const extra = String(metadata.extra_type ?? "");
  if (extra === "tool_cancelled") return "cancelled";
  if (extra === "tool_timeout") return "timeout";
  return null;
}

export function buildTrace(trace: Entry[], streaming: boolean): Item[] {
  const items: Item[] = [];
  const positions = new Map<string, number>();
  trace.forEach((entry, index) => {
    if (entry.kind === "reasoning") {
      if (!entry.text.trim()) return;
      items.push({ kind: "reasoning", key: `r-${index}`, text: entry.text });
      return;
    }
    const name = toolNameOf(entry);
    if (name === null) {
      // Match interruption notices to their tool rows and stop their spinner.
      const interrupted = interruptedStatus(entry.metadata);
      if (interrupted) {
        const target = String(entry.metadata.tool_id ?? "");
        const at = target ? positions.get(target) : undefined;
        const item = at === undefined ? undefined : items[at];
        if (item?.kind === "tool") {
          item.call.status = interrupted;
          item.call.finished = true;
        }
      }
      items.push({
        kind: "notice",
        key: `n-${index}`,
        content: entry.content,
        metadata: entry.metadata,
      });
      return;
    }
    const meta = entry.metadata;
    const id = String(meta.tool_id ?? "");
    const key = id || `t-${index}`;
    const at = positions.get(key);
    if (at === undefined) {
      positions.set(key, items.length);
      const seed: ToolCall = {
        key,
        name,
        status: "running",
        args: null,
        result: null,
        error: null,
        finished: false,
      };
      items.push({ kind: "tool", key, call: foldCall(seed, entry) });
      return;
    }
    const item = items[at];
    if (item.kind === "tool") item.call = foldCall(item.call, entry);
  });

  if (!streaming) {
    // Mark leftover running tools as unknown when the turn ends.
    for (const item of items) {
      if (item.kind === "tool" && item.call.status === "running") {
        item.call.status = "unknown";
        item.call.error = item.call.error ?? "本轮结束时没有收到结果";
      }
    }
  }
  return items;
}

/** First line, short enough for a collapsed row. */
function snippet(text: string, limit = 90): string {
  const line = text.split("\n").find((item) => item.trim()) ?? "";
  const body = line.trim();
  return body.length > limit ? `${body.slice(0, limit)}…` : body;
}

function StatusIcon({ status }: { status: ToolCall["status"] }) {
  if (status === "running") {
    return (
      <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
    );
  }
  if (status === "failed") {
    return <CircleAlertIcon className="size-3.5 text-destructive" />;
  }
  if (status === "cancelled") {
    return (
      <CircleSlashIcon className="size-3.5 text-amber-600 dark:text-amber-400" />
    );
  }
  if (status === "timeout") {
    return (
      <TimerIcon className="size-3.5 text-amber-600 dark:text-amber-400" />
    );
  }
  if (status === "unknown") {
    return <HelpCircleIcon className="size-3.5 text-muted-foreground" />;
  }
  return (
    <CheckIcon className="size-3.5 text-emerald-600 dark:text-emerald-400" />
  );
}

function Row({
  icon,
  label,
  open,
  onToggle,
  children,
  trailing,
}: {
  icon: React.ReactNode;
  label: React.ReactNode;
  open?: boolean;
  onToggle?: () => void;
  children?: React.ReactNode;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div
        role={onToggle ? "button" : undefined}
        tabIndex={onToggle ? 0 : undefined}
        onClick={onToggle}
        onKeyDown={(event) => {
          if (onToggle && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            onToggle();
          }
        }}
        className={cn(
          "flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 text-xs",
          onToggle && "cursor-pointer transition-colors hover:bg-muted/50",
        )}
      >
        <span className="shrink-0">{icon}</span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {trailing}
        {onToggle && (
          <ChevronDownIcon
            className={cn(
              "size-3 shrink-0 text-muted-foreground transition-transform",
              open && "rotate-180",
            )}
          />
        )}
      </div>
      {open && children}
    </div>
  );
}

function ReasoningRow({
  text,
  duration,
  live,
}: {
  text: string;
  duration: string | null;
  /** Still being written: show it, so streaming is not a spinner with no text. */
  live: boolean;
}) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? live;
  return (
    <Row
      icon={<BrainIcon className="size-3.5 text-muted-foreground" />}
      open={open}
      onToggle={() => setOverride(!open)}
      label={
        <span className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-medium">思考过程</span>
          {duration && (
            <span
              className="shrink-0 font-mono text-[11px] text-muted-foreground"
              title="本轮花在思考上的时间（浏览器侧计时）"
            >
              {duration}
            </span>
          )}
          {!open && (
            <span className="min-w-0 truncate text-xs text-muted-foreground">
              {snippet(text, 80)}
            </span>
          )}
        </span>
      }
    >
      <div className="mt-1 ml-1.5 max-h-64 overflow-y-auto rounded-md border border-border bg-muted/30 px-2.5 py-2 text-[0.8rem] leading-6 whitespace-pre-wrap text-foreground/85">
        {text}
      </div>
    </Row>
  );
}

function ToolRow({
  call,
  running,
  onCancel,
}: {
  call: ToolCall;
  /** Present while the server still holds this call; only then can it be cancelled. */
  running?: RunningTool;
  onCancel?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    const body = call.result ?? call.args;
    if (!body) return;
    if (await copyText(body)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("复制失败，请手动选中后复制");
    }
  };

  const state =
    call.status === "running"
      ? "运行中…"
      : call.status === "failed"
        ? "失败"
        : call.status === "cancelled"
          ? "已取消"
          : call.status === "timeout"
            ? "已超时"
            : call.status === "unknown"
              ? "未返回"
              : "完成";
  const muted = call.status === "cancelled" || call.status === "timeout";

  return (
    <Row
      icon={<StatusIcon status={call.status} />}
      open={open}
      onToggle={() => setOpen((prev) => !prev)}
      label={
        <span className="flex min-w-0 items-center gap-2">
          {/* Name first: the arguments alone do not say which tool produced them. */}
          <code
            className={cn(
              "shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]",
              call.status === "failed" && "text-destructive",
            )}
          >
            {call.name}
          </code>
          {call.args && (
            <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">
              {snippet(call.args, 100)}
            </span>
          )}
          <span
            className={cn(
              "shrink-0 text-[11px]",
              call.status === "failed"
                ? "text-destructive"
                : muted
                  ? "text-amber-600 dark:text-amber-400"
                  : "text-muted-foreground",
            )}
          >
            {state}
          </span>
        </span>
      }
      trailing={
        running && onCancel ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 shrink-0 gap-1 px-1.5 text-[11px] text-muted-foreground hover:text-destructive"
            title="终止这次工具调用；本轮继续，模型会收到你填写的原因"
            onClick={(event) => {
              // The row itself toggles; the button must not also expand it.
              event.stopPropagation();
              onCancel();
            }}
          >
            <BanIcon className="size-3" />
            取消
          </Button>
        ) : call.result ? (
          <span className="hidden max-w-56 shrink-0 truncate font-mono text-[11px] text-muted-foreground sm:block">
            {snippet(call.result, 60)}
          </span>
        ) : null
      }
    >
      <div className="mt-1 ml-1.5 flex flex-col gap-1 rounded-md border border-border bg-muted/30 px-2 py-1.5">
        <div className="flex gap-2 text-[11px] text-muted-foreground">
          <span className="shrink-0">工具</span>
          <span className="min-w-0 break-all">{call.name}</span>
        </div>
        <div className="flex gap-2 text-[11px] text-muted-foreground">
          <span className="shrink-0">tool_id</span>
          <code className="min-w-0 break-all">{call.key || "(none)"}</code>
        </div>
        {running && (
          <div className="flex gap-2 text-[11px] text-muted-foreground">
            <span className="shrink-0">超时</span>
            <span>
              {running.timeout > 0
                ? `${running.timeout} 秒后自动终止`
                : "不限制"}
            </span>
          </div>
        )}
        {call.args && (
          <div className="flex flex-col gap-0.5 border-t border-border/60 pt-1">
            <span className="text-[11px] text-muted-foreground">参数</span>
            <pre className="max-h-40 overflow-auto rounded bg-background/70 px-2 py-1 font-mono text-[11px] leading-5 break-all whitespace-pre-wrap text-foreground">
              {call.args}
            </pre>
          </div>
        )}
        {call.result && (
          <div className="flex flex-col gap-0.5 border-t border-border/60 pt-1">
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <span>返回</span>
              <span>
                {call.result.split("\n").length} 行 · {call.result.length} 字符
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto h-6 gap-1 px-1.5 text-[11px]"
                onClick={copy}
              >
                <CopyIcon className="size-3" />
                {copied ? "已复制" : "复制"}
              </Button>
            </div>
            <pre className="max-h-72 overflow-auto rounded bg-background/70 px-2 py-1 font-mono text-[11px] leading-5 break-all whitespace-pre-wrap text-foreground">
              {call.result}
            </pre>
          </div>
        )}
        {call.error && (
          <div className="font-mono text-[11px] break-all whitespace-pre-wrap text-destructive">
            {call.error}
          </div>
        )}
      </div>
    </Row>
  );
}

/** A step boundary, a system notice, a skill trigger — anything but a tool. */
function NoticePill({
  content,
  metadata,
}: {
  content: string;
  metadata: Record<string, unknown>;
}) {
  const kind = String(metadata.type ?? "");
  const extra = String(metadata.extra_type ?? "");
  const label =
    kind === "skill_call"
      ? `技能 ${String(metadata.skill_name ?? "")}`
      : kind === "system" && extra === "tool_call_limit_extended"
        ? "预算"
        : extra === "tool_cancelled"
          ? "取消"
          : extra === "tool_timeout"
            ? "超时"
            : kind === "system"
              ? "系统"
              : kind && kind.startsWith("step")
                ? `步骤 ${String(metadata.extra_type ?? "")}`
                : "事件";
  return (
    <div className="flex items-start gap-2 text-xs text-muted-foreground">
      <InfoIcon className="mt-0.5 size-3.5 shrink-0" />
      <span className="font-medium text-foreground">{label}</span>
      <span className="min-w-0 flex-1 truncate" title={content}>
        {content}
      </span>
    </div>
  );
}

/**
 * Context compaction, as the pair of states it actually has.
 *
 * The fold happens inside the first request, so `compaction_started` is what a
 * running turn shows and `compaction_finished` replaces it once the summary is
 * in the history — the user should be able to tell why earlier messages stopped
 * being sent verbatim.
 */
function CompactionNotice({
  content,
  metadata,
}: {
  content: string;
  metadata: Record<string, unknown>;
}) {
  const extra = String(metadata.extra_type ?? "");
  const done = extra === "compaction_finished";
  const skipped = extra === "compaction_skipped";
  return (
    <div className="flex items-start gap-2 text-xs text-muted-foreground">
      {done ? (
        <CheckIcon className="mt-0.5 size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
      ) : skipped ? (
        <InfoIcon className="mt-0.5 size-3.5 shrink-0" />
      ) : (
        <Loader2Icon className="mt-0.5 size-3.5 animate-spin" />
      )}
      <span className="shrink-0 font-medium text-foreground">
        {done ? (
          <>
            <span className="text-muted-foreground line-through decoration-muted-foreground/40">
              Compacting Conversations
            </span>{" "}
            <span aria-hidden>→</span> Compacted Conversations
          </>
        ) : skipped ? (
          "Compacting Conversations"
        ) : (
          "Compacting Conversations…"
        )}
      </span>
      <span className="min-w-0 flex-1 truncate" title={content}>
        {content}
      </span>
    </div>
  );
}

function thinkingLabel(ms?: number): string | null {
  if (!ms || ms < 200) return null;
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Every step of the turn, oldest first.
 *
 * ``message.trace`` is what the stream recorded, so the order is the order the
 * events arrived. A message loaded from disk has no trace (the transcript only
 * ever stored the reasoning blob and the events separately), so it falls back to
 * "reasoning, then steps" — the one ordering the stored data can justify.
 */
export function Trace({
  message,
  streaming,
  runningTools,
  onCancelTool,
}: {
  message: ChatMessage;
  streaming: boolean;
  /** Calls the server is executing right now, so the row can offer 取消. */
  runningTools?: Record<string, RunningTool>;
  onCancelTool?: (tool: RunningTool) => void;
}) {
  const trace: Entry[] =
    message.trace.length > 0
      ? message.trace
      : [
          ...(message.reasoning
            ? [{ kind: "reasoning" as const, text: message.reasoning }]
            : []),
          ...message.events.map((event) => ({
            kind: "event" as const,
            content: event.content,
            metadata: event.metadata,
          })),
        ];
  const items = buildTrace(trace, streaming);
  if (items.length === 0) return null;

  const compactionSettled = message.events.some((event) => {
    const extra = String(event.metadata.extra_type ?? "");
    return extra === "compaction_finished" || extra === "compaction_skipped";
  });
  const duration = thinkingLabel(message.thinkingMs);

  return (
    <div className="mb-2 flex flex-col gap-0.5 border-l border-border/70 pl-2">
      {items.map((item, index) => {
        if (item.kind === "reasoning") {
          // Stream only the newest reasoning step; render earlier steps as history.
          const live = streaming && index === items.length - 1;
          return (
            <ReasoningRow
              key={item.key}
              text={item.text}
              duration={index === 0 ? duration : null}
              live={live}
            />
          );
        }
        if (item.kind === "tool") {
          const running = runningTools?.[item.call.key];
          const live = item.call.status === "running" ? running : undefined;
          return (
            <ToolRow
              key={item.key}
              call={item.call}
              running={live}
              onCancel={
                live && onCancelTool ? () => onCancelTool(live) : undefined
              }
            />
          );
        }
        const extra = String(item.metadata.extra_type ?? "");
        if (extra === "compaction_started" && compactionSettled) return null;
        return (
          <div key={item.key} className="px-1.5 py-1">
            {extra.startsWith("compaction_") ? (
              <CompactionNotice
                content={item.content}
                metadata={item.metadata}
              />
            ) : (
              <NoticePill content={item.content} metadata={item.metadata} />
            )}
          </div>
        );
      })}
    </div>
  );
}
