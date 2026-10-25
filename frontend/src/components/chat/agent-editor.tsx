/**
 * Agent preset editor.
 *
 * The file on disk is the source of truth, so this edits raw Markdown rather
 * than a form that tries to re-serialize it. The backend validates before
 * writing, which keeps a half-finished YAML block from making the preset
 * silently disappear from the list.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckIcon,
  CircleAlertIcon,
  FileTextIcon,
  Loader2Icon,
  LockIcon,
  PlusIcon,
  SaveIcon,
  Trash2Icon,
  TriangleAlertIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { InfoHint } from "@/components/ui/info-hint";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownOrSource } from "@/components/chat/markdown-or-source";
import { PresetEditor } from "@/components/chat/preset-editor";
import {
  api,
  type AgentDetail,
  type AgentPreset,
  type MCPSource,
  type ToolCatalogEntry,
  type WorkflowSpec,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * ``tools:`` entries that match no tool at all.
 *
 * A whitelist is matched by name against the *mixed* pool — a plugin tool and
 * an MCP tool are the same thing here — so a rule can legitimately point at a
 * server that is not connected right now. Hence "warning, not error": saving
 * stays possible, and the text says why the match may fail today.
 */
function UnknownToolsWarning({ patterns }: { patterns: string[] }) {
  if (patterns.length === 0) return null;
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
      <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 flex-1 break-all">
        <span className="font-medium">
          {patterns.length} 条 tools 规则没有匹配到任何工具：
        </span>
        <span className="font-mono">{patterns.join("、")}</span>
        <span className="mt-0.5 block text-muted-foreground">
          匹配范围是插件工具 + MCP 工具（名字对上就行）。如果那个 MCP 服务器现在没连上，
          它的工具不在池里，这条规则本身可能是对的；保存不会被拦。
        </span>
      </span>
    </div>
  );
}

export function AgentEditor({
  agents,
  workflows,
  tools,
  mcp,
  onChanged,
}: {
  agents: AgentPreset[];
  workflows: WorkflowSpec[];
  tools: ToolCatalogEntry[];
  mcp: MCPSource[];
  onChanged: () => void;
}) {
  // Every preset is listed, built-ins included: they are what a fresh install
  // runs, and hiding them made the app look like it had no default mode. A
  // file with the same name shadows a built-in rather than replacing it.
  const listed = useMemo(() => {
    const builtin = agents.filter((agent) => agent.scope === "builtin");
    const others = agents.filter((agent) => agent.scope !== "builtin");
    return [...builtin, ...others];
  }, [agents]);
  const [selected, setSelected] = useState<string | null>(listed[0]?.name ?? null);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  //: A switch that would throw away unsaved edits, parked until it is confirmed.
  const [pendingSwitch, setPendingSwitch] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newScope, setNewScope] = useState<"user" | "workspace">("user");

  const load = useCallback(async (name: string) => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await api.getAgent(name);
      setDetail(loaded);
      setDraft(loaded.raw);
      setDirty(false);
      if (loaded.unknown_tools?.length) {
        // Console echo: the same fact the banner shows, where a preset author
        // is likely to be looking while editing YAML.
        console.warn(
          `[agent] 预设 ${loaded.name} 的 tools 规则没有匹配到任何工具：`,
          loaded.unknown_tools,
        );
      }
    } catch (exc) {
      setError(String(exc));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selected) void load(selected);
    else {
      setDetail(null);
      setDraft("");
      setDirty(false);
      setLoading(false);
    }
  }, [selected, load]);

  const save = async () => {
    if (!selected || !detail) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveAgent(selected, draft);
      setDirty(false);
      toast.success(`${saved.title} 已保存`);
      onChanged();
      await load(selected);
    } catch (exc) {
      const message = String(exc).replace(/^Error:\s*/, "");
      setError(message);
      toast.error("预设未保存：内容校验失败");
    } finally {
      setSaving(false);
    }
  };

  const create = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await api.createAgent(name, name, newScope);
      setNewName("");
      onChanged();
      setSelected(created.name);
      toast.success("已创建");
    } catch (exc) {
      toast.error(`创建失败：${String(exc)}`);
    }
  };

  const remove = async () => {
    if (!selected) return;
    try {
      const result = await api.deleteAgent(selected);
      onChanged();
      setSelected(listed.find((agent) => agent.name !== selected)?.name ?? null);
      toast.success(
        result.restored_builtin
          ? "已删除覆盖文件，恢复为内建预设"
          : "已删除",
      );
    } catch (exc) {
      toast.error(`删除失败：${String(exc)}`);
    }
  };

  const toolNames = tools.map((tool) => tool.name);
  const mcpNames = mcp.map((server) => server.name);
  const isBuiltin = detail?.scope === "builtin";
  //: "`react`（标准 ReAct）" — shown so a preset author sees what each name buys.
  const workflowLabel = (workflow: WorkflowSpec) =>
    `${workflow.name}（${workflow.mode_label}）`;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 md:flex-row">
      <div className="flex max-h-56 shrink-0 flex-col gap-2 md:max-h-none md:h-full md:w-56">
        <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
          {listed.map((agent) => (
            <button
              key={agent.name}
              type="button"
              onClick={() => {
                if (dirty && agent.name !== selected) {
                  setPendingSwitch(agent.name);
                  return;
                }
                setSelected(agent.name);
              }}
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
                selected === agent.name && "bg-accent",
              )}
            >
              {agent.scope === "builtin" ? (
                <LockIcon className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1">
                  <span className="min-w-0 flex-1 truncate" title={agent.title}>
                    {agent.title}
                  </span>
                  {(agent.unknown_tools?.length ?? 0) > 0 && (
                    <span title={`${agent.unknown_tools?.length} 条 tools 规则没匹配到工具`}>
                      <TriangleAlertIcon className="size-3 shrink-0 text-amber-500" />
                    </span>
                  )}
                </span>
                <span className="block truncate text-[11px] text-muted-foreground">
                  {agent.scope === "builtin"
                    ? "内建 · 不可修改"
                    : `${agent.scope === "workspace" ? "工作区" : "用户"} · ${
                        agent.workflow_pinned
                          ? `固定 ${agent.workflow}`
                          : "跟随全局工作流"
                      }${agent.read_only ? " · 只读" : ""}${
                        agent.shadows_builtin ? " · 覆盖内建" : ""
                      }`}
                </span>
              </span>
            </button>
          ))}
          {listed.length === 0 && (
            <p className="px-2 py-1 text-xs text-muted-foreground">还没有预设</p>
          )}
        </div>

        <div className="flex shrink-0 flex-col gap-1.5 border-t border-border pt-2">
          <Label htmlFor="new-agent" className="text-[11px] text-muted-foreground">
            新建预设
          </Label>
          <Input
            id="new-agent"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="coder"
            className="h-8 text-xs"
          />
          <select
            value={newScope}
            onChange={(event) =>
              setNewScope(event.target.value as "user" | "workspace")
            }
            className="h-8 rounded-md border border-border bg-background px-2 text-xs outline-none"
          >
            <option value="user">用户目录 ~/.amrita/agents</option>
            <option value="workspace">工作区 .amrita/agents</option>
          </select>
          <Button size="sm" variant="outline" onClick={create}>
            <PlusIcon />
            创建
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
        {isBuiltin && detail ? (
          <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
            <div className="flex items-start gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
              <LockIcon className="mt-0.5 size-3.5 shrink-0" />
              <span>
                <span className="font-medium text-foreground">内建模式，不可修改或删除。</span>{" "}
                它由程序提供，磁盘上没有对应的 Markdown 文件，所以保存和删除接口都会直接拒绝。
                要定制，请新建一个同名预设把它覆盖掉——删掉那个文件就会恢复成这里的样子。
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <h3 className="text-sm font-medium">{detail.title}</h3>
              <p className="text-xs text-muted-foreground">
                {detail.description || "（无描述）"}
              </p>
            </div>
            <UnknownToolsWarning patterns={detail.unknown_tools ?? []} />
            <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 text-xs">
              <dt className="text-muted-foreground">名称</dt>
              <dd className="font-mono">{detail.name}</dd>
              <dt className="text-muted-foreground">工具</dt>
              <dd>
                {detail.tools === null
                  ? "不过滤，全部已启用工具"
                  : `${detail.tools.length} 条规则`}
              </dd>
              <dt className="text-muted-foreground">访问模式</dt>
              <dd>
                {detail.access === null
                  ? "三种都可用（由输入框决定）"
                  : detail.access.join(" / ")}
              </dd>
              <dt className="text-muted-foreground">工作流</dt>
              <dd>
                {detail.workflow_pinned
                  ? `固定 ${detail.workflow_title ?? detail.workflow}`
                  : "跟随全局工作流"}
              </dd>
              <dt className="text-muted-foreground">会话记忆</dt>
              <dd>{detail.memory === "session" ? "已挂载" : "未挂载"}</dd>
            </dl>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs text-muted-foreground">系统提示词（只读）</span>
              <MarkdownOrSource
                content={detail.prompt ?? ""}
                emptyHint="（无正文，使用程序内置的默认提示词）"
                sourceHeightClass="max-h-64"
              />
            </div>
          </div>
        ) : loading ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            载入中…
          </div>
        ) : detail ? (
          <>
            <div className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
              <code className="min-w-0 flex-1 truncate" title={detail.path ?? ""}>
                {detail.path}
              </code>
              {dirty && <span className="text-amber-600">未保存</span>}
              <Button
                size="sm"
                variant="ghost"
                onClick={remove}
                title={
                  detail.shadows_builtin
                    ? "删除这个覆盖文件，恢复内建预设"
                    : "删除预设文件"
                }
              >
                <Trash2Icon className="size-3.5" />
                {detail.shadows_builtin ? "恢复内建" : "删除"}
              </Button>
              <Button size="sm" onClick={save} disabled={saving || !dirty}>
                {saving ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <SaveIcon className="size-3.5" />
                )}
                保存
              </Button>
            </div>
            <UnknownToolsWarning patterns={detail.unknown_tools ?? []} />
            <PresetEditor
              value={draft}
              onChange={(next) => {
                setDraft(next);
                setDirty(true);
                setError(null);
              }}
            />
            {error ? (
              <div className="flex shrink-0 items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
                <CircleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
                <span className="break-all whitespace-pre-wrap">{error}</span>
              </div>
            ) : (
              <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-muted/30 px-2 py-1.5 text-[11px] text-muted-foreground">
                <CheckIcon className="size-3.5 shrink-0" />
                <span className="min-w-0 flex-1">
                  {detail.workflow_pinned ? `固定 ${detail.workflow}` : "跟随全局工作流"} ·{" "}
                  工具 {toolNames.length} · MCP {mcpNames.length}
                  {(detail.unknown_tools?.length ?? 0) > 0 && (
                    <span className="ml-1 text-amber-600 dark:text-amber-400">
                      · {detail.unknown_tools?.length} 条规则没匹配上
                    </span>
                  )}
                </span>
                <InfoHint label="预设字段说明" align="end">
                  <span className="flex flex-col gap-1.5">
                    <span>
                      工作流可用：
                      {workflows.map((item) => workflowLabel(item)).join("、")}。
                    </span>
                    <span>
                      <code>workflow</code> 决定循环方式：<code>react</code> 一轮一次请求；
                      <code>react-step</code> 先拆解成多步计划再逐步执行。不写则跟随全局，
                      写了就锁死，全局开关对它无效。
                    </span>
                    <span>
                      <code>access</code> 写 <code>[readonly]</code> 即限制为只读；不写表示
                      三种权限都可用。
                    </span>
                    <span>
                      <code>memory: session</code> 挂载一个会话临时目录
                      （<code>~/.amrita/sessions/&lt;id&gt;/memory</code>），计划写在里面，
                      随对话删除；只读预设也能用，因为它不属于工作区。
                    </span>
                    <span>
                      正文就是系统提示词，没有全局提示词覆盖它。正文中以 <code>\---</code> 开头
                      表示一行 <code>---</code>。
                    </span>
                  </span>
                </InfoHint>
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <FileTextIcon className="size-5" />
            选择或新建一个预设
          </div>
        )}
      </div>

      <ConfirmDialog
        open={pendingSwitch !== null}
        onOpenChange={(open) => {
          if (!open) setPendingSwitch(null);
        }}
        title="放弃未保存的修改？"
        description={
          <>
            当前预设的正文有改动还没保存，切过去就丢了。
            <br />
            想留下的话先点「保存」。
          </>
        }
        confirmLabel="放弃并切换"
        destructive
        onConfirm={() => {
          if (pendingSwitch === null) return;
          setDirty(false);
          setSelected(pendingSwitch);
        }}
      />
    </div>
  );
}
