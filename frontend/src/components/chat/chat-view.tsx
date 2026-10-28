import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownIcon,
  ArrowUpRightIcon,
  CircleAlertIcon,
  CopyIcon,
  DatabaseIcon,
  FileTextIcon,
  GaugeIcon,
  GitBranchIcon,
  Loader2Icon,
  PencilIcon,
  RotateCcwIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Markdown } from "@/components/chat/markdown";
import { ChatFooter } from "@/components/chat/chat-footer";
import { Trace } from "@/components/chat/trace";
import { AuditPanel } from "@/components/chat/audit-panel";
import { TodoPanel } from "@/components/chat/todo-panel";
import type { SettingsTab } from "@/components/chat/settings-dialog";
import {
  Composer,
  type AgentOption,
  type CommandOption,
  type GuardState,
  type PendingAttachment,
  type Permission,
  type WorkspaceOption,
} from "@/components/chat/composer";
import { Button } from "@/components/ui/button";
import { PromptDialog } from "@/components/ui/prompt-dialog";
import {
  type ChatMessage,
  useChatSocket as useChatSocketFor,
} from "@/hooks/use-chat-socket";
import { api } from "@/lib/api";
import type { ModelOption, RunningTool } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

/** Least -> most permissive; mirrors the backend's ordering. */
const ACCESS_ORDER: Permission[] = ["readonly", "workspace", "full"];

/**
 * The mode that will actually be used: the preset's declaration caps what the
 * composer asks for. Doing this in the UI too means the chip never promises a
 * level the server would silently drop.
 */
function narrowPermission(
  declared: Permission[] | null | undefined,
  requested: Permission,
): Permission {
  if (!declared || declared.length === 0) return requested;
  const allowed = ACCESS_ORDER.filter((mode) => declared.includes(mode));
  if (allowed.length === 0) return "readonly";
  const within = allowed.filter(
    (mode) => ACCESS_ORDER.indexOf(mode) <= ACCESS_ORDER.indexOf(requested),
  );
  return within.length > 0 ? within[within.length - 1] : allowed[0];
}

/** The empty transcript, before the first message.
 *
 * Each prompt is a request the assistant can answer immediately: a starter that
 * says "tell me your idea" only asks the user to type again, which is what the
 * empty composer already does.
 */
const STARTERS = [
  {
    title: "解释一个概念",
    prompt: "用一个生活里的比喻，解释一下「机会成本」。",
  },
  {
    title: "写段文案",
    prompt: "给一家周三下午总是没客人的小咖啡馆，写一条朋友圈文案。",
  },
  {
    title: "帮我做决定",
    prompt: "两个差不多的选择之间纠结时，给我 3 个真正能用的判断方法。",
  },
  {
    title: "拆个计划",
    prompt: "我想每天匀出 30 分钟学点新东西，帮我排一个一周的计划。",
  },
] as const;

function EmptyState({
  onPick,
  disabled,
}: {
  onPick: (text: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="mt-10 flex flex-col items-center gap-8">
      <div className="flex flex-col items-center gap-4 text-center">
        <img
          src="/assets/brand/amrita-black.png"
          alt="Amrita"
          draggable={false}
          className="size-32 select-none object-contain dark:hidden"
        />
        <img
          src="/assets/brand/amrita-white.png"
          alt="Amrita"
          draggable={false}
          className="hidden size-32 select-none object-contain dark:block"
        />
        <h2 className="text-2xl font-medium tracking-tight sm:text-3xl">
          嗨~👋 我们从哪里开始呢？
        </h2>
      </div>

      <div className="grid w-full gap-2 sm:grid-cols-2">
        {STARTERS.map((item) => (
          <button
            key={item.title}
            type="button"
            disabled={disabled}
            onClick={() => onPick(item.prompt)}
            className="group flex flex-col gap-1 rounded-xl border border-border bg-card px-3.5 py-3 text-left transition-colors hover:border-primary/40 hover:bg-muted/40 disabled:pointer-events-none disabled:opacity-50"
          >
            <span className="flex items-center gap-1.5 text-sm font-medium">
              {item.title}
              <ArrowUpRightIcon className="size-3.5 opacity-0 transition-opacity group-hover:opacity-60" />
            </span>
            <span className="line-clamp-1 text-xs leading-5 text-muted-foreground">
              {item.prompt}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * What the placeholder says while a turn is thinking.
 *
 * The first entry is the normal label and the rest are easter eggs: a rare
 * surprise is fun, a random label every time is noise.
 */
const THINKING_QUIPS = ["思考中…", "Amria还在…", "正在玩 MC…", "正在摸鱼…"];

function pickThinkingLabel(): string {
  if (Math.random() > 0.15) return THINKING_QUIPS[0];
  return THINKING_QUIPS[
    1 + Math.floor(Math.random() * (THINKING_QUIPS.length - 1))
  ];
}

/** Placeholder shown between "send" and the first token. */
function ThinkingIndicator() {
  const [label] = useState(pickThinkingLabel);
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2Icon className="size-3.5 animate-spin" />
      <span className="thinking-sheen font-medium">{label}</span>
    </div>
  );
}

/** Tall enough to be worth collapsing: about a dozen lines of chat text. */
const USER_COLLAPSE_PX = 352;

/**
 * A sent user message: rendered as Markdown, clamped when long, with any
 * credential-shaped string masked.
 *
 * User text used to be `whitespace-pre-wrap`, which kept pasted code and lists
 * as one grey wall. Markdown is what the reply already gets, and it is the same
 * content either way — the copy button still copies the original source.
 */
function UserMessageBody({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const body = useRef<HTMLDivElement>(null);

  // Use scrollHeight to detect overflow in the clamped message.
  useLayoutEffect(() => {
    const element = body.current;
    if (!element) return;
    setCollapsed(element.scrollHeight > USER_COLLAPSE_PX + 40);
    setExpanded(false);
  }, [content]);

  const hidden = collapsed && !expanded;

  return (
    <div className="flex max-w-[min(80%,42rem)] flex-col items-start">
      <div className="relative w-full overflow-hidden rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-primary-foreground shadow-sm">
        <div
          ref={body}
          className={cn(
            "md md-user",
            hidden && "max-h-[22rem] overflow-hidden",
          )}
        >
          <Markdown content={content} maskSecrets />
        </div>
        {hidden && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-primary to-transparent" />
        )}
      </div>
      {collapsed && (
        <Button
          variant="ghost"
          size="sm"
          className="mt-0.5 h-7 px-2 text-[11px] text-muted-foreground"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {expanded ? "收起" : `展开全文（${content.length} 字）`}
        </Button>
      )}
    </div>
  );
}

/**
 * The line that ends the part of the transcript the model no longer receives.
 *
 * A compaction notice means everything above it was folded into the summary, so
 * those bubbles stay readable but no longer take part in the conversation.
 */
function ReadOnlyDivider() {
  return (
    <div
      role="separator"
      aria-label="以上为只读历史"
      className="flex items-center gap-3 text-[11px] text-muted-foreground"
    >
      <span className="h-px flex-1 bg-border" />
      <span className="shrink-0">以上为只读历史，已折叠进摘要</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}

/** Whether this bubble carries a settled compaction notice. */
function endsFoldedHistory(message: ChatMessage): boolean {
  return message.events.some((event) => {
    const extra = String(event.metadata.extra_type ?? "");
    return extra === "compaction_finished" || extra === "compaction_skipped";
  });
}

function MessageBubble({
  message,
  onBranch,
  onRetry,
  onEdit,
  runningTools,
  onCancelTool,
}: {
  message: ChatMessage;
  /** Fork the conversation at this message. Offered for both roles. */
  onBranch?: (entryId: string) => void;
  /** Re-send the prompt behind a failed turn. Only wired up for errors. */
  onRetry?: () => void;
  /**
   * Rewrite this prompt and send it again. Only offered for the last prompt
   * the agent never answered.
   */
  onEdit?: (text: string) => void;
  /** Live tool calls, so a running row can offer 取消. */
  runningTools?: Record<string, RunningTool>;
  onCancelTool?: (tool: RunningTool) => void;
}) {
  const [copied, setCopied] = useState(false);
  // Keep the single editable prompt draft local to its message.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const isUser = message.role === "user";

  const copy = async () => {
    if (await copyText(message.content)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("复制失败，请手动选中后复制");
    }
  };

  if (isUser) {
    const images = (message.attachments ?? []).filter(
      (item) => item.kind === "image" && item.url,
    );
    const files = (message.attachments ?? []).filter(
      (item) => item.kind !== "image",
    );

    const submitEdit = () => {
      const text = draft.trim();
      if (!text || !onEdit) return;
      setEditing(false);
      onEdit(text);
    };

    if (editing) {
      return (
        <div className="flex flex-col items-end gap-2">
          <textarea
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter keeps the newline; Esc abandons.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submitEdit();
              } else if (event.key === "Escape") {
                event.preventDefault();
                setEditing(false);
              }
            }}
            rows={Math.min(12, Math.max(3, draft.split("\n").length))}
            aria-label="编辑消息"
            className="w-full max-w-[min(80%,42rem)] resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm leading-relaxed outline-none focus:border-ring/60"
          />
          <div className="flex items-center gap-1.5">
            {(message.attachments ?? []).length > 0 && (
              <span className="text-[11px] text-muted-foreground">
                附件会一并重发
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              取消
            </Button>
            <Button size="sm" onClick={submitEdit} disabled={!draft.trim()}>
              保存并重发
            </Button>
          </div>
        </div>
      );
    }

    return (
      <div className="group flex flex-col items-end gap-1.5">
        {images.length > 0 && (
          <div className="flex max-w-[min(80%,42rem)] flex-wrap justify-end gap-1.5">
            {images.map((image) => (
              <img
                key={image.id}
                src={image.url ?? ""}
                alt={image.name}
                className="max-h-48 rounded-xl border border-border object-contain"
              />
            ))}
          </div>
        )}
        {files.length > 0 && (
          <div className="flex max-w-[min(80%,42rem)] flex-wrap justify-end gap-1.5">
            {files.map((file) => (
              <span
                key={file.id}
                title={file.path ?? file.name}
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-[11px] text-muted-foreground"
              >
                <FileTextIcon className="size-3" />
                {file.name}
              </span>
            ))}
          </div>
        )}
        {message.content && <UserMessageBody content={message.content} />}
        {!message.streaming && (
          <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 max-sm:opacity-100">
            {copied && (
              <span className="text-[11px] text-muted-foreground">已复制</span>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={copy}
              aria-label="复制"
              title="复制原文（含被遮挡的敏感串）"
            >
              <CopyIcon className="size-3.5" />
            </Button>
            {onEdit && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => {
                  setDraft(message.content);
                  setEditing(true);
                }}
                aria-label="编辑并重发"
                title="编辑并重发：回滚到这条消息之前，再按新内容重新提问"
              >
                <PencilIcon className="size-3.5" />
              </Button>
            )}
            {onBranch && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => onBranch(message.id)}
                aria-label="从这里分叉"
                title="从这里分叉：新会话保留到这条消息为止"
              >
                <GitBranchIcon className="size-3.5" />
              </Button>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="group min-w-0">
      {/* Thinking and tool calls are one timeline: the order is the
            information, so neither is hoisted above the answer. */}
      <Trace
        message={message}
        streaming={message.streaming}
        runningTools={runningTools}
        onCancelTool={onCancelTool}
      />

      {message.content ? (
        <Markdown content={message.content} streaming={message.streaming} />
      ) : message.streaming ? (
        <ThinkingIndicator />
      ) : null}

      {message.error && (
        <div className="mt-2 flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <CircleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="break-all whitespace-pre-wrap">{message.error}</p>
            {onRetry && (
              <Button
                variant="outline"
                size="sm"
                className="mt-2 h-7 gap-1 border-destructive/40 bg-background px-2 text-[11px] text-foreground hover:bg-muted"
                onClick={onRetry}
                title="重新发送上一次的提问"
              >
                <RotateCcwIcon className="size-3" />
                重试
              </Button>
            )}
          </div>
        </div>
      )}

      {!message.streaming && message.content && (
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
          {message.usage?.total_tokens ? (
            <span className="inline-flex items-center gap-1">
              <DatabaseIcon className="size-3" />
              {message.usage.total_tokens} tok
              {message.usage.tokens_per_second
                ? ` · ${message.usage.tokens_per_second} tok/s`
                : ""}
              {message.usage.rounds > 1
                ? ` · ${message.usage.rounds} 次请求`
                : ""}
              {message.usage.tool_calls > 0
                ? ` · ${message.usage.tool_calls} 工具`
                : ""}
            </span>
          ) : null}
          <span className="ml-auto flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 max-sm:opacity-100">
            {onBranch && (
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={() => onBranch(message.id)}
                aria-label="从这里分叉"
                title="从这里分叉：新会话保留到这条消息为止"
              >
                <GitBranchIcon className="size-3.5" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={copy}
              aria-label="复制"
            >
              <CopyIcon className="size-3.5" />
            </Button>
            {copied && <span>已复制</span>}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * The run spent its tool-call budget and is parked here.
 *
 * Deliberately *not* a modal: the run is paused, not broken, and the transcript
 * behind it is exactly what the user needs to read before deciding.
 *
 * Three answers, because "stop" is not one thing. Winding down still costs a
 * model request and produces a rushed answer; cutting the turn off costs nothing
 * and says so. Choosing one of those for the user was the old behaviour, and it
 * was wrong in both directions.
 *
 * "继续迭代" appends LLM requests to *this* turn, so the copy says how many and
 * promises the agent keeps working with its context intact.
 */
function ToolLimitCard({
  budget,
  extension,
  timeout,
  onContinue,
  onGraceful,
  onAbort,
}: {
  budget: number;
  extension: number;
  timeout: number;
  onContinue: () => void;
  onGraceful: () => void;
  onAbort: () => void;
}) {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-1">
      <div className="rounded-xl border border-border bg-card px-4 py-3 shadow-sm">
        <div className="flex items-start gap-2.5">
          <GaugeIcon className="mt-0.5 size-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">继续迭代？</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              本轮的 LLM 请求预算（{budget} 次）已经用完，Agent
              停在这里等你决定。 继续会在<strong>本轮</strong>内追加 {extension}{" "}
              次 LLM 请求，模型带着现有上下文接着做；
              优雅收尾让模型把已经查到的东西写完；直接中断则立刻停在这里，
              不再让模型多发一次请求。
              {timeout > 0 &&
                ` ${Math.round(timeout)} 秒内不选，按优雅收尾处理。`}
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                onClick={onContinue}
                title={`本轮再追加 ${extension} 次 LLM 请求，上下文不丢`}
              >
                继续迭代（本轮 +{extension} 次请求）
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={onGraceful}
                title="不再调用工具，让模型用已有信息写完这一轮"
              >
                优雅收尾
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-muted-foreground hover:text-destructive"
                onClick={onAbort}
                title="立刻停止本轮，不再请求模型"
              >
                直接中断
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ChatView({
  sessionId,
  onTitleHint,
  workspaces,
  activeWorkspace,
  agents,
  models,
  activeAgent,
  activeModel,
  contextVersion = 0,
  onSelectWorkspace,
  onAddWorkspace,
  onSelectAgent,
  onSelectModel,
  onModelsChanged,
  onOpenSettings,
  onBranch,
  onRunningChange,
}: {
  sessionId: string;
  onTitleHint: (title: string) => void;
  workspaces: WorkspaceOption[];
  activeWorkspace: string | null;
  agents: AgentOption[];
  models: ModelOption[];
  activeAgent: string;
  activeModel: string | null;
  /** Changes when settings close; re-runs fetches this view only does once. */
  contextVersion?: number;
  onSelectWorkspace: (path: string) => void;
  onAddWorkspace: () => void;
  onSelectAgent: (name: string) => void;
  onSelectModel: (name: string) => void;
  /** Re-read the provider list after a model attribute was changed. */
  onModelsChanged: () => void;
  onOpenSettings: (tab?: SettingsTab) => void;
  /** Fork the session at a history entry, keeping that message. */
  onBranch: (entryId: string) => void;
  /** Tell the sidebar this conversation started or stopped working. */
  onRunningChange?: (running: boolean) => void;
}) {
  const {
    messages,
    connection,
    busy,
    send,
    stop,
    reload,
    plan,
    liveUsage,
    totals,
    lastUsage,
    contextMessages,
    pendingConfirms,
    todos,
    clearTodos,
    sendConfirm,
    retry,
    editPrompt,
    limitRequest,
    resolveLimit,
    runningTools,
    cancelTool,
    injectCapability,
    pendingPrompt,
    stopping,
    offerPrompt,
    revokePrompt,
    revokedDraft,
    clearRevokedDraft,
  } = useChatSocketFor(sessionId);
  const [permission, setPermission] = useState<Permission>("workspace");
  // The tool call the user asked to cancel; the dialog collects the reason.
  const [cancelTarget, setCancelTarget] = useState<RunningTool | null>(null);
  // Fetch backend commands so plugin commands need no frontend changes.
  const [commands, setCommands] = useState<CommandOption[]>([]);
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  // Offer only preset-approved modes; read-only presets show a locked label.
  const allowedPermissions = useMemo<Permission[]>(() => {
    const declared = agents.find((item) => item.name === activeAgent)?.access;
    if (!declared || declared.length === 0) return ACCESS_ORDER;
    return ACCESS_ORDER.filter((mode) => declared.includes(mode));
  }, [agents, activeAgent]);

  const effectivePermission = narrowPermission(allowedPermissions, permission);

  /**
   * The one prompt that can still be rewritten: the last user message, with
   * nothing but failed turns after it.
   *
   * Same rule as the server, so the button is never offered for a message the
   * API would refuse — and never offered while a turn is running, when the
   * context is still in use.
   */
  const editableId = useMemo(() => {
    if (busy) return null;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role !== "user") continue;
      const answered = messages
        .slice(index + 1)
        .some(
          (item) =>
            item.role === "assistant" &&
            (item.content.trim() !== "" || !item.error),
        );
      return !answered && !message.streaming ? message.id : null;
    }
    return null;
  }, [busy, messages]);

  /**
   * Save one attribute of the active model from the switcher.
   *
   * Only the changed field is sent, so a stale form elsewhere cannot blank the
   * rest of the entry; the provider list is re-read so the chip stays truthful.
   */
  const configureModel = async (
    key: string,
    patch: { thinking_effort: string | null },
  ) => {
    const model = models.find((item) => item.key === key);
    if (!model) return;
    try {
      await api.patchModel(model.provider_id, model.model, patch);
      toast.success(
        patch.thinking_effort
          ? `已把 ${model.model} 的思考强度设为 ${patch.thinking_effort}`
          : `已恢复 ${model.model} 的默认思考强度`,
      );
      onModelsChanged();
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    }
  };

  /**
   * Context folding now happens inside the turn (``/compact`` is a message),
   * so this component no longer calls the REST endpoint.
   */
  const guard: GuardState = useMemo(() => {
    if (models.length === 0) {
      return {
        blocked: "还没有配置模型。先到设置 -> 模型 里添加 Provider 和模型。",
        needsModel: true,
      };
    }
    if (!activeModel) {
      return { blocked: "还没有选中模型。", needsModel: true };
    }
    // Allow keyless local providers; the provider screen handles the warning.
    if (uploading > 0) {
      return { blocked: `正在上传附件（${uploading}）…`, needsModel: false };
    }
    return { blocked: null, needsModel: false };
  }, [models, activeModel, uploading]);

  /**
   * Upload files the way the backend wants them: images as data URLs so a
   * multimodal model receives pixels, text as a stored file so a long paste
   * does not have to be re-read on every round.
   */
  const attach = async (files: File[]) => {
    setUploading((prev) => prev + files.length);
    for (const file of files) {
      try {
        if (file.type.startsWith("image/")) {
          if (file.size > 8 * 1024 * 1024) {
            toast.error(`${file.name} 超过 8 MiB，已跳过`);
            continue;
          }
          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
          });
          const stored = await api.uploadAttachment({
            kind: "image",
            name: file.name,
            data_url: dataUrl,
          });
          setPending((prev) => [
            ...prev,
            {
              id: stored.id,
              kind: "image",
              name: stored.name,
              size: stored.size,
              url: stored.url ?? dataUrl,
              path: stored.path,
            },
          ]);
        } else {
          const text = await file.text();
          const stored = await api.uploadAttachment({
            kind: "text",
            name: file.name,
            text,
          });
          setPending((prev) => [
            ...prev,
            {
              id: stored.id,
              kind: "text",
              name: stored.name,
              size: stored.size,
              path: stored.path,
            },
          ]);
        }
      } catch (error) {
        toast.error(`${file.name}: ${String(error).replace(/^Error:\s*/, "")}`);
      } finally {
        setUploading((prev) => Math.max(0, prev - 1));
      }
    }
  };

  useEffect(() => {
    let cancelled = false;
    api
      .listCommands()
      .then((result) => {
        if (cancelled) return;
        setCommands(
          result.commands.map((command) => ({
            name: command.name,
            title: command.title,
            description: command.description,
            source: command.source,
          })),
        );
      })
      .catch(() => setCommands([]));
    return () => {
      cancelled = true;
    };
    // Plugins and skills are edited in settings; re-read when that page closes.
  }, [contextVersion]);

  // Report this session's busy state; App polls background sessions.
  useEffect(() => {
    onRunningChange?.(busy);
  }, [busy, onRunningChange]);

  useLayoutEffect(() => {
    if (!stickToBottom.current) return;
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stickToBottom.current = distance < 80;
      setShowJump(distance > 240);
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => node.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as {
        sessionId: string;
        title: string;
      };
      if (detail.sessionId === sessionId) onTitleHint(detail.title);
    };
    window.addEventListener("amrita:session-title", handler);
    return () => window.removeEventListener("amrita:session-title", handler);
  }, [onTitleHint, sessionId]);

  const jumpToBottom = () => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
    stickToBottom.current = true;
  };

  /** One send path for the composer and the starters, so both behave alike. */
  const submit = (text: string) => {
    const files = pending.map((item) => ({
      id: item.id,
      kind: item.kind,
      name: item.name,
      url: item.url ?? null,
      path: item.path ?? null,
    }));
    if (busy) {
      // Queue for the next turn and stop the current one; a second press escalates the stop.
      const queued = offerPrompt(
        text,
        files,
        stopping ? "immediate" : "graceful",
      );
      if (!queued) {
        toast.error("连接已断开，无法排队；请等本轮结束后再发送");
        return false;
      }
      stickToBottom.current = true;
      setPending([]);
      return true;
    }
    const sent = send(text, files, activeModel, effectivePermission);
    if (sent) {
      stickToBottom.current = true;
      setPending([]);
    }
    return sent;
  };

  const pickStarter = (text: string) => {
    // ``send`` refuses while the guard blocks; say why instead of doing nothing.
    if (guard.blocked) {
      toast.error(guard.blocked);
      return;
    }
    submit(text);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scroller} className="relative min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
          {messages.length === 0 && (
            <EmptyState onPick={pickStarter} disabled={busy || uploading > 0} />
          )}
          {messages.map((message) => (
            <div key={message.id} className="flex flex-col gap-2">
              <MessageBubble
                message={message}
                onBranch={onBranch}
                runningTools={runningTools}
                onCancelTool={setCancelTarget}
                // Allow retry only after a failed turn has finished.
                onRetry={message.error && !busy ? retry : undefined}
                onEdit={
                  message.id === editableId
                    ? (text) => {
                        void editPrompt(message.id, text);
                      }
                    : undefined
                }
              />
              {endsFoldedHistory(message) && <ReadOnlyDivider />}
            </div>
          ))}
        </div>

        {showJump && (
          <Button
            variant="outline"
            size="sm"
            onClick={jumpToBottom}
            className="sticky bottom-3 mx-auto flex shadow-md"
          >
            <ArrowDownIcon />
            回到底部
          </Button>
        )}
      </div>

      <div className="shrink-0">
        <AuditPanel requests={pendingConfirms} onSubmit={sendConfirm} />
        {limitRequest && (
          <ToolLimitCard
            budget={limitRequest.budget}
            extension={limitRequest.extension}
            timeout={limitRequest.timeout}
            onContinue={() => resolveLimit("continue")}
            onGraceful={() => resolveLimit("graceful")}
            onAbort={() => resolveLimit("abort")}
          />
        )}
        <TodoPanel todos={todos} onClear={clearTodos} />
        <Composer
          busy={busy}
          stopping={stopping}
          pendingPrompt={pendingPrompt}
          inject={injectCapability}
          seed={revokedDraft}
          onSeedConsumed={clearRevokedDraft}
          workspaces={workspaces}
          activeWorkspace={activeWorkspace}
          agents={agents}
          activeAgent={activeAgent}
          models={models}
          activeModel={activeModel}
          permission={effectivePermission}
          allowedPermissions={allowedPermissions}
          attachments={pending}
          guard={guard}
          onSend={submit}
          onStop={stop}
          onRevoke={revokePrompt}
          onAttach={(files) => {
            void attach(files);
          }}
          onRemoveAttachment={(id) =>
            setPending((prev) => prev.filter((item) => item.id !== id))
          }
          onSelectWorkspace={onSelectWorkspace}
          onAddWorkspace={onAddWorkspace}
          onSelectAgent={onSelectAgent}
          onSelectModel={onSelectModel}
          onSelectPermission={setPermission}
          commands={commands}
          onConfigureModel={(key, patch) => {
            void configureModel(key, patch);
          }}
          onCommand={(name) => {
            // Handle local commands here; the composer sends backend commands as messages.
            if (name === "model") onOpenSettings("providers");
            if (name === "blocked") toast.error(guard.blocked ?? "无法发送");
          }}
        />
        <ChatFooter
          busy={busy}
          plan={plan}
          live={liveUsage}
          last={lastUsage}
          totals={totals}
          connection={connection}
          contextMessages={contextMessages}
          onReload={() => {
            void reload();
            toast.success("已重新载入");
          }}
        />
      </div>
      <PromptDialog
        open={cancelTarget !== null}
        onOpenChange={(open) => {
          if (!open) setCancelTarget(null);
        }}
        title="取消这次工具调用？"
        description={
          cancelTarget ? (
            <>
              将终止 <code className="font-mono">{cancelTarget.tool}</code>，
              但本轮不会结束：原因会作为这次调用的结果交给模型，
              它可以用别的方式接着做。
            </>
          ) : null
        }
        label="取消原因"
        placeholder="例如：这条命令不该跑，换成只读的检查"
        confirmLabel="取消这次调用"
        onSubmit={(reason) => {
          const target = cancelTarget;
          if (!target) return null;
          return cancelTool(target.tool_id, reason)
            ? null
            : "没有连上服务端，请稍后再试";
        }}
      />
    </div>
  );
}
