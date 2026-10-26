/**
 * Run metadata strip below the composer.
 *
 * It answers, at a glance: what ran, how fast, what it cost, and how full the
 * context is. Every item is optional — a fresh session renders nothing rather
 * than placeholders.
 *
 * Kept to one line on purpose: the strip sits under the composer, and a second
 * line there reads as a second toolbar ("太长了" was the exact complaint).
 */

import {
  ActivityIcon,
  CoinsIcon,
  DatabaseIcon,
  GitBranchIcon,
  GaugeIcon,
  HardDriveIcon,
  RotateCcwIcon,
} from "lucide-react";

import { MockHint } from "@/components/chat/mock-help";
import type { LiveUsage, RunPlan } from "@/hooks/use-chat-socket";
import type { TurnUsage, UsageTotals } from "@/lib/api";
import { MOCK_WORKFLOW_TITLE } from "@/lib/mock-stream";
import { cn } from "@/lib/utils";

function compact(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)}k`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

function money(usage: TurnUsage): string | null {
  if (usage.cost === null || usage.cost === undefined || usage.cost === 0) return null;
  const value = usage.cost < 0.01 ? usage.cost.toFixed(5) : usage.cost.toFixed(4);
  return `${value} ${usage.currency}`;
}

function duration(ms: number | null | undefined): string | null {
  if (!ms || ms < 0) return null;
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function Chip({
  icon: Icon,
  children,
  title,
  className,
}: {
  icon: typeof GaugeIcon;
  children: React.ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      className={cn("inline-flex shrink-0 items-center gap-1", className)}
      title={title}
    >
      <Icon className="size-3" />
      {children}
    </span>
  );
}

/**
 * Share of the prompt that came from cache.
 *
 * The denominator is ``cache_hit + prompt_tokens``: ``prompt_tokens`` is the
 * *total* prompt size, of which ``cache_hit`` was served from cache. Dividing by
 * ``prompt_tokens`` alone would understate the rate and, worse, change meaning
 * between providers that count cached tokens inside the prompt and ones that
 * add them on top.
 */
function cacheRate(usage: TurnUsage | null): number | null {
  if (!usage) return null;
  const hit = usage.cache_hit ?? 0;
  const total = hit + (usage.prompt_tokens ?? 0);
  if (hit <= 0 || total <= 0) return null;
  return Math.round((hit / total) * 100);
}

export function RunMeta({
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
  /** Re-read the session from disk; rendered at the right end of the strip. */
  onReload?: () => void;
  /** Messages the next request will carry; shown in the context chip. */
  contextMessages?: number | null;
}) {
  const current = busy ? live : last;
  const tokens = current?.total_tokens ?? 0;
  const speed = last?.tokens_per_second ?? null;
  const cachePercent = cacheRate(last);
  const cost = last ? money(last) : null;
  // Use persisted counts because live tool messages make the running total lag.
  const messages = contextMessages ?? last?.context_messages ?? null;

  const hasAnything =
    Boolean(plan) || tokens > 0 || Boolean(last) || Boolean(totals?.turns);

  return (
    <div
      className={cn(
        // Keep the stats strip on one row and clip its least important tail.
        "flex min-w-0 flex-1 items-center gap-2.5 overflow-hidden text-[11px] whitespace-nowrap text-muted-foreground",
        !hasAnything && "justify-between",
      )}
    >
      {busy && (
        <Chip icon={ActivityIcon}>
          <span className="font-medium text-foreground">生成中</span>
        </Chip>
      )}
      {plan && (
        <>
          <Chip icon={GitBranchIcon} title={plan.workflowTitle}>
            {plan.workflowTitle}
          </Chip>
          {plan.workflowTitle === MOCK_WORKFLOW_TITLE && <MockHint side="top" />}
          {!busy && last && (
            <Chip icon={ActivityIcon}>
              {last.rounds} 次请求{last.steps > 0 ? ` ${last.steps} 步` : ""}
            </Chip>
          )}
        </>
      )}
      {speed !== null && (
        <Chip icon={GaugeIcon} title="输出速度">
          {speed} tok/s
        </Chip>
      )}
      {tokens > 0 && (
        <Chip icon={DatabaseIcon} title="本轮消耗">
          {compact(tokens)} tok
        </Chip>
      )}
      {cachePercent !== null && (
        <Chip
          icon={HardDriveIcon}
          title={`缓存命中 ${compact(last?.cache_hit ?? 0)} tok，占提示词的 ${cachePercent}%`}
          className="text-emerald-600 dark:text-emerald-400"
        >
          命中 {cachePercent}%
        </Chip>
      )}
      {cost && (
        <Chip icon={CoinsIcon} title="本轮花费">
          {cost}
        </Chip>
      )}
      {!busy &&
        last?.context_percent !== null &&
        last?.context_percent !== undefined && (
          <Chip
            icon={GaugeIcon}
            title={[
              `上下文 ${compact(last.context_tokens ?? 0)} / ${compact(
                last.max_context ?? 0,
              )} tok`,
              messages !== null
                ? `下一次请求会带 ${messages} 条消息`
                : "消息数未知（本轮还没结束）",
              "百分比是占模型窗口的比例",
            ]
              .filter(Boolean)
              .join("\n")}
          >
            {Math.round(last.context_percent)}%
            {messages !== null && (
              <span>· {messages} 条</span>
            )}
          </Chip>
        )}
      {!busy && last && duration(last.duration_ms) && (
        <span className="shrink-0">用时 {duration(last.duration_ms)}</span>
      )}
      {totals && totals.turns > 0 && !busy && (
        <span className="ml-auto shrink-0" title="本会话累计">
          会话累计 {compact(totals.total_tokens)} tok · {totals.turns} 轮
          {totals.cache_hit > 0
            ? ` · 命中 ${Math.round(
                (totals.cache_hit /
                  Math.max(totals.cache_hit + totals.prompt_tokens, 1)) *
                  100,
              )}%`
            : ""}
        </span>
      )}
      {!busy && !totals?.turns && (
        <span className="ml-auto shrink-0">
          {connection === "open"
            ? "已连接"
            : connection === "connecting"
              ? "连接中…"
              : "连接已断开，正在重连"}
        </span>
      )}
      {onReload && (
        <button
          type="button"
          onClick={onReload}
          title="从磁盘重读历史并重连；正在跑的一轮会从头回放"
          aria-label="重新载入"
          className={cn(
            "ml-auto inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-muted hover:text-foreground",
            // Avoid extra button margin when totals already occupy the row.
            totals?.turns && !!busy === false && "ml-0",
          )}
        >
          <RotateCcwIcon className="size-3" />
          重新载入
        </button>
      )}
    </div>
  );
}
