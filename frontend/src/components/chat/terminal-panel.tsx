/**
 * Terminal sessions.
 *
 * A session here is *logical*: each command runs in a fresh process, but the
 * directory, the configured executable and the recent commands belong to the
 * session. That is enough to answer "where am I, what did it run last, what did
 * it print", which is what was missing when the only interface was a tool call
 * buried in the transcript.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  RefreshCwIcon,
  SquareTerminalIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { AnsiText } from "@/components/ui/ansi-text";
import { InfoHint } from "@/components/ui/info-hint";
import { api, type ShellSession } from "@/lib/api";
import { cn } from "@/lib/utils";

/** ``unix``/``bash`` reads badly; show the family only when it adds meaning. */
function shellLabel(session: ShellSession): string {
  const flavour = session.flavour ?? "";
  if (!flavour || flavour === session.shell) return session.shell;
  return `${session.shell} · ${flavour}`;
}

function ExitBadge({ code, timedOut }: { code: number | null; timedOut: boolean }) {
  if (timedOut) {
    return (
      <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive">
        超时
      </span>
    );
  }
  if (code === null) {
    return (
      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
        无退出码
      </span>
    );
  }
  return (
    <span
      className={cn(
        "rounded px-1.5 py-0.5 text-[10px]",
        code === 0
          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
          : "bg-destructive/10 text-destructive",
      )}
    >
      exit {code}
    </span>
  );
}

function SessionCard({
  session,
  onClose,
}: {
  session: ShellSession;
  onClose: (id: string) => void;
}) {
  const [openRun, setOpenRun] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border px-3 py-2">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-muted">
          <SquareTerminalIcon className="size-3.5 text-muted-foreground" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="font-medium">{shellLabel(session)}</span>
            <code className="text-[10px] text-muted-foreground">{session.id}</code>
            {session.executable_configured && (
              <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                自定义解释器
              </span>
            )}
            <span className="text-[11px] text-muted-foreground">
              {session.runs} 次命令
            </span>
          </div>
          <div className="truncate text-xs text-muted-foreground" title={session.cwd}>
            {session.cwd}
          </div>
          <div className="truncate text-[11px] text-muted-foreground" title={session.executable}>
            {session.executable || "PATH 上的默认解释器"}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="断开终端会话"
          title="断开：清除已记住的目录与历史（不影响文件）"
          onClick={() => onClose(session.id)}
        >
          <XIcon className="size-3.5" />
        </Button>
      </div>

      {session.history.length === 0 ? (
        <p className="text-xs text-muted-foreground">还没有执行过命令</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {[...session.history].reverse().map((run, index) => {
            const key = `${run.at}-${index}`;
            const expanded = openRun === key;
            return (
              <li key={key} className="rounded-md border border-border/60">
                <button
                  type="button"
                  onClick={() => setOpenRun(expanded ? null : key)}
                  className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-muted/40"
                  aria-expanded={expanded}
                >
                  {expanded ? (
                    <ChevronDownIcon className="size-3 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRightIcon className="size-3 shrink-0 text-muted-foreground" />
                  )}
                  <ExitBadge code={run.exit_code} timedOut={run.timed_out} />
                  <code className="min-w-0 flex-1 truncate text-[11px]">{run.command}</code>
                  <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                    {run.elapsed_ms} ms
                  </span>
                </button>
                {expanded && (
                  <div className="border-t border-border/60 px-2 py-1.5">
                    <div className="mb-1 text-[10px] text-muted-foreground">{run.cwd}</div>
                    <pre className="max-h-56 overflow-auto rounded bg-[#0d1117] px-2 py-1.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[#c9d1d9]">
                      {run.output.trim() ? (
                        <AnsiText text={run.output.trim()} />
                      ) : (
                        "(无输出)"
                      )}
                    </pre>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function TerminalPanel() {
  const [sessions, setSessions] = useState<ShellSession[]>([]);
  const [available, setAvailable] = useState<string[]>([]);
  const [spool, setSpool] = useState("");
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      const data = await api.shells();
      setSessions(data.sessions);
      setAvailable(data.available);
      setSpool(data.spool);
    } catch (error) {
      toast.error(`加载终端失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    // Sessions only change when a command runs, which the user does from the
    // chat; a slow poll is enough and avoids a second event channel.
    const timer = window.setInterval(() => void reload(), 4000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const close = async (id: string) => {
    try {
      await api.closeShell(id);
      toast.success("已断开终端会话");
      await reload();
    } catch (error) {
      toast.error(`断开失败：${String(error)}`);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <SquareTerminalIcon className="size-3.5" />
          终端会话
        </span>
        <span className="text-[11px] text-muted-foreground">
          可用 shell：{available.join(" / ") || "无"}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          aria-label="刷新"
          onClick={() => void reload()}
        >
          <RefreshCwIcon className="size-3.5" />
        </Button>
        <InfoHint label="终端会话说明" align="end">
          <span className="flex flex-col gap-1.5">
            <span>
              每条命令都在新进程里执行，但目录、解释器和历史属于会话，
              所以「上一条 <code>cd</code> 到哪」能延续到下一次。
            </span>
            <span>
              想在哪个解释器里跑，去「插件 → shell-pwsh / shell-unix → 配置」
              填可执行文件路径（例如 <code>/bin/bash</code>）和初始化脚本；
              bash 与 zsh 共用同一个 unix 插件，方言在配置里选。
            </span>
            <span>
              「断开」只是忘掉会话记住的目录与历史，不删任何文件；
              同一工作区下次再调用会建一个新会话。
            </span>
            {spool && (
              <span className="text-muted-foreground">
                被截断的完整输出留在 <code>{spool}</code>。
              </span>
            )}
          </span>
        </InfoHint>
      </div>

      {loading && sessions.length === 0 && (
        <p className="text-sm text-muted-foreground">加载中…</p>
      )}
      {!loading && sessions.length === 0 && (
        <p className="text-sm text-muted-foreground">
          还没有终端会话。Agent 调用 shell 工具、或你在输入框里让它跑命令之后，这里会出现。
        </p>
      )}
      {sessions.map((session) => (
        <SessionCard key={session.id} session={session} onClose={close} />
      ))}
    </div>
  );
}
