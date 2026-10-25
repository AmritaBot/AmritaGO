/**
 * Composer: the input surface shown in the reference UI.
 *
 * Layout mirrors it exactly — a column of two rows:
 *
 *   row 1: workspace switcher · agent-mode switcher
 *   row 2 (box): placeholder/"+" · permission chip …… model switcher · send
 *
 * Behaviours that matter:
 * - ``/`` opens a command palette filtered as you type (only commands that
 *   actually exist are listed — no invented entries).
 * - ``@`` is reserved for file/thread mentions; here it just hints, because
 *   the workspace file index is not part of this app yet.
 * - The send button is a circle and only enabled for non-empty input, matching
 *   the reference.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArchiveIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleAlertIcon,
  FileTextIcon,
  FolderIcon,
  FolderPlusIcon,
  ImageIcon,
  LockIcon,
  PlusIcon,
  ShieldCheckIcon,
  ShieldIcon,
  ShieldAlertIcon,
  SquareIcon,
  TerminalIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import type { AccessMode, ModelOption } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Permission = AccessMode;

/** Pastes at or above this length become a file instead of text. */
export const LONG_PASTE = 4000;

export interface AgentOption {
  name: string;
  title: string;
  description: string;
  workflow?: string | null;
  workflowTitle?: string | null;
  /** Which loop the preset runs: 直接回答 / 标准 ReAct / 步进 ReAct. */
  workflowMode?: string | null;
  workflowModeLabel?: string | null;
  /** ``false`` when the preset follows the globally active workflow. */
  workflowPinned?: boolean;
  /** Modes the preset declares; ``null`` means "all of them". */
  access?: Permission[] | null;
}

export interface WorkspaceOption {
  name: string;
  path: string;
}

export interface PendingAttachment {
  id: string;
  kind: "image" | "text";
  name: string;
  size: number;
  url?: string | null;
  path?: string | null;
}

export interface GuardState {
  /** Why sending is blocked right now, if it is. */
  blocked: string | null;
  /** True when there is no usable model configured. */
  needsModel: boolean;
}

export interface CommandOption {
  name: string;
  title: string;
  description: string;
  icon?: "compact" | "clear" | "permission" | "model" | "export" | "feedback" | "file";
}

const PERMISSIONS: Record<
  Permission,
  { label: string; description: string; icon: typeof ShieldIcon }
> = {
  readonly: { label: "仅可查看", description: "只读，不改动文件", icon: ShieldIcon },
  workspace: {
    label: "工作区内修改",
    description: "可写入当前工作区",
    icon: ShieldCheckIcon,
  },
  full: { label: "完全权限", description: "不限制写入范围", icon: ShieldAlertIcon },
};

const COMMAND_ICONS = {
  compact: ArchiveIcon,
  clear: Trash2Icon,
  permission: ShieldIcon,
  model: TerminalIcon,
  export: ArrowUpIcon,
  feedback: ArrowUpIcon,
  file: PlusIcon,
};

export function Composer({
  busy,
  workspaces,
  activeWorkspace,
  agents,
  activeAgent,
  models,
  activeModel,
  permission,
  allowedPermissions,
  attachments,
  guard,
  onSend,
  onStop,
  onAttach,
  onRemoveAttachment,
  onSelectWorkspace,
  onAddWorkspace,
  onSelectAgent,
  onSelectModel,
  onSelectPermission,
  onCommand,
}: {
  busy: boolean;
  workspaces: WorkspaceOption[];
  activeWorkspace: string | null;
  agents: AgentOption[];
  activeAgent: string;
  models: ModelOption[];
  activeModel: string | null;
  permission: Permission;
  /** Modes the active preset may use. Empty/undefined means "no restriction". */
  allowedPermissions?: Permission[];
  attachments: PendingAttachment[];
  guard: GuardState;
  onSend: (text: string) => void;
  onStop: () => void;
  onAttach: (files: File[]) => void;
  onRemoveAttachment: (id: string) => void;
  onSelectWorkspace: (path: string) => void;
  onAddWorkspace: () => void;
  onSelectAgent: (name: string) => void;
  onSelectModel: (name: string) => void;
  onSelectPermission: (value: Permission) => void;
  onCommand: (name: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState<"workspace" | "agent" | "permission" | "model" | null>(
    null,
  );
  const [commandIndex, setCommandIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const commands: CommandOption[] = useMemo(
    () => [
      {
        name: "permission",
        title: "权限",
        description: "切换权限预设（沙箱模式与审批策略）",
        icon: "permission",
      },
      {
        name: "model",
        title: "模型",
        description: "选择本会话使用的模型",
        icon: "model",
      },
      {
        name: "agent",
        title: "Agent",
        description: "切换 Agent 预设",
        icon: "permission",
      },
      {
        name: "compact",
        title: "压缩上下文",
        description: "把较早的消息折叠成摘要，腾出上下文空间",
        icon: "compact",
      },
      {
        name: "clear",
        title: "清空",
        description: "清空当前会话",
        icon: "clear",
      },
    ],
    [],
  );

  /** ``/xxx`` at the very start of the input opens the palette. */
  const slashQuery = useMemo(() => {
    const match = /^\/([a-zA-Z\u4e00-\u9fa5]*)$/.exec(draft.trim());
    return match ? match[1].toLowerCase() : null;
  }, [draft]);

  const filteredCommands = useMemo(() => {
    if (slashQuery === null) return [];
    return commands.filter(
      (command) =>
        command.name.includes(slashQuery) || command.title.includes(slashQuery),
    );
  }, [commands, slashQuery]);

  useEffect(() => setCommandIndex(0), [slashQuery]);

  // Close popovers on outside click.
  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(null);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const activeWorkspaceName =
    workspaces.find((item) => item.path === activeWorkspace)?.name ?? "默认工作区";
  const agentLabel =
    agents.find((item) => item.name === activeAgent)?.title ?? "标准模式";
  const activeEntry = models.find((item) => item.key === activeModel) ?? null;
  const activeLabel = activeEntry
    ? `${activeEntry.model} · ${activeEntry.provider}`
    : activeModel
      ? activeModel.split("::").at(-1) ?? activeModel
      : "未选择模型";

  const canSend =
    !busy && !guard.blocked && (draft.trim().length > 0 || attachments.length > 0);

  const submit = () => {
    if (busy) return;
    if (!draft.trim() && attachments.length === 0) return;
    if (guard.blocked) {
      onCommand("blocked");
      return;
    }
    if (slashQuery !== null && filteredCommands.length > 0) {
      runCommand(filteredCommands[commandIndex]);
      return;
    }
    onSend(draft);
    setDraft("");
  };

  const runCommand = (command: CommandOption) => {
    setDraft("");
    if (command.name === "permission") setOpen("permission");
    else if (command.name === "model") setOpen("model");
    else if (command.name === "agent") setOpen("agent");
    else onCommand(command.name);
  };

  /**
   * Paste handling. An image becomes an attachment; a very long text paste
   * becomes a file instead of a wall of text in a textarea. Both keep the
   * composer usable and stop a stray Ctrl+V from turning a prompt into
   * something the model has to re-read every round.
   */
  const onPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(event.clipboardData.items);
    const images = items
      .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (images.length > 0) {
      event.preventDefault();
      onAttach(images);
      return;
    }

    const text = event.clipboardData.getData("text/plain");
    if (text.length >= LONG_PASTE) {
      const hasSelection =
        textareaRef.current !== null &&
        textareaRef.current.selectionStart !== textareaRef.current.selectionEnd;
      if (!hasSelection) {
        event.preventDefault();
        onAttach([
          new File([text], `pasted-${new Date().toISOString().slice(11, 19)}.txt`, {
            type: "text/plain",
          }),
        ]);
      }
    }
  };

  const PermissionIcon = PERMISSIONS[permission].icon;
  // A preset that declares a single access mode is not a suggestion: the chip
  // becomes a label, so nobody spends a turn discovering the server will
  // narrow their request back down anyway.
  const choiceModes: Permission[] =
    allowedPermissions && allowedPermissions.length > 0
      ? allowedPermissions
      : (Object.keys(PERMISSIONS) as Permission[]);
  const permissionLocked = choiceModes.length === 1;

  return (
    <div ref={rootRef} className="relative mx-auto w-full max-w-3xl px-4 pb-4">
      {/* row 1 — workspace + agent */}
      <div className="mb-2 flex items-center gap-1">
        <Popover
          open={open === "workspace"}
          onOpenChange={(value) => setOpen(value ? "workspace" : null)}
          trigger={
            <>
              <FolderIcon className="size-4" />
              <span>{activeWorkspaceName}</span>
              <ChevronDownIcon className="size-3.5 opacity-60" />
            </>
          }
        >
          {workspaces.map((workspace) => (
            <MenuItem
              key={workspace.path}
              active={workspace.path === activeWorkspace}
              onClick={() => {
                onSelectWorkspace(workspace.path);
                setOpen(null);
              }}
              icon={<FolderIcon className="size-4" />}
              title={workspace.name}
              subtitle={workspace.path}
            />
          ))}
          <MenuItem
            onClick={() => {
              onAddWorkspace();
              setOpen(null);
            }}
            icon={<FolderPlusIcon className="size-4" />}
            title="添加工作区…"
          />
        </Popover>

        <Popover
          open={open === "agent"}
          onOpenChange={(value) => setOpen(value ? "agent" : null)}
          trigger={
            <>
              <TerminalIcon className="size-4" />
              <span>{agentLabel}</span>
              <ChevronDownIcon className="size-3.5 opacity-60" />
            </>
          }
          wide
        >
          {agents.map((agent) => (
            <MenuItem
              key={agent.name}
              active={agent.name === activeAgent}
              onClick={() => {
                onSelectAgent(agent.name);
                setOpen(null);
              }}
              title={agent.title}
              subtitle={[
                // Which loop the preset runs — "计划模式" and "编码模式" differ
                // in workflow, and that is the part worth surfacing.
                agent.workflowPinned === false
                  ? "跟随全局工作流"
                  : agent.workflowModeLabel,
                agent.description,
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          ))}
        </Popover>
      </div>

      {/* row 2 — input box */}
      <div className="relative rounded-2xl border border-border bg-card shadow-sm focus-within:border-ring/60">
        {slashQuery !== null && filteredCommands.length > 0 && (
          <div className="absolute bottom-full left-0 z-30 mb-2 w-full overflow-hidden rounded-xl border border-border bg-popover shadow-lg">
            <div className="px-3 py-1.5 text-[11px] text-muted-foreground">指令</div>
            {filteredCommands.map((command, index) => {
              const Icon = COMMAND_ICONS[command.icon ?? "compact"];
              return (
                <button
                  key={command.name}
                  type="button"
                  onMouseEnter={() => setCommandIndex(index)}
                  onClick={() => runCommand(command)}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-2 text-left text-sm",
                    index === commandIndex && "bg-accent",
                  )}
                >
                  <Icon className="size-4 shrink-0 text-muted-foreground" />
                  <span>{command.title}</span>
                  <span className="text-xs text-muted-foreground">{command.name}</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {command.description}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2">
            {attachments.map((item) => (
              <span
                key={item.id}
                className="group inline-flex max-w-56 items-center gap-1.5 rounded-md border border-border bg-muted/40 py-1 pr-1 pl-2 text-[11px]"
              >
                {item.kind === "image" ? (
                  <ImageIcon className="size-3 shrink-0 text-muted-foreground" />
                ) : (
                  <FileTextIcon className="size-3 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate" title={item.name}>
                  {item.name}
                </span>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(item.id)}
                  className="rounded p-0.5 text-muted-foreground hover:bg-accent"
                  aria-label={`移除 ${item.name}`}
                >
                  <XIcon className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onPaste={onPaste}
          onKeyDown={(event) => {
            if (slashQuery !== null && filteredCommands.length > 0) {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setCommandIndex((prev) => (prev + 1) % filteredCommands.length);
                return;
              }
              if (event.key === "ArrowUp") {
                event.preventDefault();
                setCommandIndex(
                  (prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length,
                );
                return;
              }
            }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="描述你想要构建的内容，/ 调用指令，@ 文件或对话；可直接粘贴图片"
          className="max-h-56 min-h-[76px] w-full resize-none bg-transparent px-4 pt-3 pb-2 text-sm leading-6 outline-none placeholder:text-muted-foreground max-sm:min-h-[96px] max-sm:text-base max-sm:leading-7"
        />

        <div className="flex items-center gap-1 px-2 pt-1 pb-2 max-sm:gap-1.5 max-sm:px-1.5 max-sm:pt-1.5 max-sm:pb-2.5">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept="image/*,text/*,.md,.json,.yaml,.yml,.log"
            className="hidden"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              if (files.length > 0) onAttach(files);
              event.target.value = "";
            }}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            className="rounded-full max-sm:size-10"
            aria-label="添加附件"
            onClick={() => fileRef.current?.click()}
          >
            <PlusIcon />
          </Button>

          {permissionLocked ? (
            <span
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs whitespace-nowrap text-muted-foreground max-sm:h-10 max-sm:text-[13px]"
              title="当前 Agent 预设固定了这个权限级别"
            >
              <PermissionIcon className="size-4" />
              <span>{PERMISSIONS[permission].label}</span>
              <LockIcon className="size-3 opacity-60" />
            </span>
          ) : (
            <Popover
              open={open === "permission"}
              onOpenChange={(value) => setOpen(value ? "permission" : null)}
              trigger={
                <>
                  <PermissionIcon className="size-4" />
                  <span>{PERMISSIONS[permission].label}</span>
                  <ChevronDownIcon className="size-3.5 opacity-60" />
                </>
              }
              triggerClassName="text-muted-foreground"
            >
              {choiceModes.map((key) => {
                const item = PERMISSIONS[key];
                const Icon = item.icon;
                return (
                  <MenuItem
                    key={key}
                    active={key === permission}
                    onClick={() => {
                      onSelectPermission(key);
                      setOpen(null);
                    }}
                    icon={<Icon className="size-4" />}
                    title={item.label}
                    subtitle={item.description}
                  />
                );
              })}
            </Popover>
          )}

          <div className="ml-auto flex items-center gap-1">
            <Popover
              open={open === "model"}
              onOpenChange={(value) => setOpen(value ? "model" : null)}
              trigger={
                <>
                  <span className="max-w-40 truncate max-sm:max-w-24" title={activeLabel}>
                    {activeLabel}
                  </span>
                  <ChevronDownIcon className="size-3.5 opacity-60" />
                </>
              }
              align="end"
              triggerClassName="text-muted-foreground"
            >
              {models.length === 0 && (
                <div className="px-3 py-2 text-xs text-muted-foreground">
                  还没有模型，去设置里添加
                </div>
              )}
              {models.map((model) => (
                <MenuItem
                  key={model.key}
                  active={model.key === activeModel}
                  onClick={() => {
                    onSelectModel(model.key);
                    setOpen(null);
                  }}
                  title={model.model}
                  subtitle={model.provider}
                />
              ))}
            </Popover>

            <Button
              size="icon"
              className="size-8 rounded-full max-sm:size-10"
              onClick={busy ? onStop : submit}
              disabled={!busy && !canSend}
              aria-label={busy ? "停止生成" : "发送"}
            >
              {busy ? (
                <SquareIcon className="size-3.5" />
              ) : (
                <ArrowUpIcon className="size-4" />
              )}
            </Button>
          </div>
        </div>

        {guard.blocked && (
          <div className="flex items-start gap-2 border-t border-border px-3 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
            <CircleAlertIcon className="mt-0.5 size-3 shrink-0" />
            <span className="min-w-0 flex-1">{guard.blocked}</span>
            {guard.needsModel && (
              <button
                type="button"
                onClick={() => onCommand("model")}
                className="shrink-0 underline underline-offset-2"
              >
                去配置
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Popover({
  open,
  onOpenChange,
  trigger,
  children,
  align = "start",
  wide = false,
  triggerClassName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: React.ReactNode;
  children: React.ReactNode;
  align?: "start" | "end";
  wide?: boolean;
  triggerClassName?: string;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm transition-colors hover:bg-accent",
          // Touch targets: 28px is fine with a mouse, not with a thumb.
          "max-sm:px-2.5 max-sm:py-2 max-sm:text-[15px]",
          open && "bg-accent",
          triggerClassName,
        )}
      >
        {trigger}
      </button>
      {open && (
        <div
          className={cn(
            "absolute bottom-full z-40 mb-1 overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-lg",
            align === "end" ? "right-0" : "left-0",
            wide ? "w-96" : "min-w-56",
            "max-w-[calc(100vw-1.5rem)]",
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

function MenuItem({
  title,
  subtitle,
  icon,
  active,
  onClick,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <Tooltip
      // The row is deliberately truncated; the tooltip is the only place the
      // sentence survives, so it repeats the title instead of summarising.
      side="left"
      align="start"
      content={
        <span className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{title}</span>
          {subtitle && <span className="text-muted-foreground">{subtitle}</span>}
        </span>
      }
    >
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent max-sm:px-3 max-sm:py-2.5"
      >
        {icon && <span className="mt-0.5 shrink-0 text-muted-foreground">{icon}</span>}
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1 text-sm max-sm:text-[15px]">
            {title}
            {active && <CheckIcon className="size-3.5 text-primary" />}
          </span>
          {subtitle && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground max-sm:text-[13px]">
              {subtitle}
            </span>
          )}
        </span>
      </button>
    </Tooltip>
  );
}
