/**
 * Plugins and workflows panel.
 *
 * Lists what the runtime registered, shows which workflow is active, and can
 * re-run activation. Developer mode is the switch that makes the backend
 * load -> unload -> load plugins on every activation, so a plugin that leaks
 * state is reported instead of silently accumulating it.
 */

import { useCallback, useEffect, useState } from "react";
import {
  BoxesIcon,
  GitBranchIcon,
  InfoIcon,
  Loader2Icon,
  LockIcon,
  RefreshCwIcon,
  Settings2Icon,
  WrenchIcon,
} from "lucide-react";
import { toast } from "sonner";

import { MockHint } from "@/components/chat/mock-help";
import { PluginConfigForm } from "@/components/chat/plugin-config-form";
import { Button } from "@/components/ui/button";
import { InfoHint } from "@/components/ui/info-hint";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  api,
  type PluginSnapshot,
  type WorkflowListing,
  type WorkflowSpec,
} from "@/lib/api";
import { isMockMode, setMockMode } from "@/lib/mock-stream";
import { cn } from "@/lib/utils";

function ModeBadge({ workflow }: { workflow: WorkflowSpec }) {
  return (
    <span
      className={cn(
        "rounded px-1 py-0.5 text-[10px]",
        workflow.mode === "step"
          ? "bg-primary/10 text-primary"
          : workflow.mode === "chat"
            ? "bg-muted text-muted-foreground"
            : "bg-muted text-muted-foreground",
      )}
      title={workflow.hint}
    >
      {workflow.mode_label}
    </span>
  );
}

export function PluginPanel({
  debug,
  onDebugChange,
}: {
  debug: boolean;
  onDebugChange: (value: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<PluginSnapshot | null>(null);
  const [listing, setListing] = useState<WorkflowListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [reloading, setReloading] = useState(false);
  //: Name of the plugin whose settings drawer is open, if any.
  const [openConfig, setOpenConfig] = useState<string | null>(null);
  //: Front-end test mode lives in localStorage; this mirrors it for the switch.
  const [mock, setMock] = useState(() => isMockMode());

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [plugins, flow] = await Promise.all([
        api.listPlugins(),
        api.listWorkflows(),
      ]);
      setSnapshot(plugins);
      setListing(flow);
    } catch (error) {
      toast.error(`加载插件失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const activateWorkflow = async (name: string) => {
    try {
      await api.activateWorkflow(name);
      const flow = await api.listWorkflows();
      setListing(flow);
      toast.success(`已切换到工作流 ${name}`);
    } catch (error) {
      // Show the backend explanation when the active preset pins a workflow.
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const reloadPlugins = async () => {
    setReloading(true);
    try {
      const updated = await api.reloadPlugins();
      setSnapshot(updated);
      await reload();
      toast.success(
        updated.debug
          ? "插件已重载（开发者模式：加载/卸载/加载一致）"
          : "插件已重载",
      );
    } catch (error) {
      toast.error(`重载失败：${String(error)}`);
    } finally {
      setReloading(false);
    }
  };

  /**
   * Enable/disable one plugin.
   *
   * The switch writes the enabled allow-list and the backend re-activates, because a
   * plugin that is not loaded has no tools, hooks or MCP providers at all —
   * filtering after the fact would leave a snapshot describing something that
   * does not exist.
   */
  const togglePlugin = async (name: string, enabled: boolean) => {
    if (!snapshot) return;
    const current = new Set(
      snapshot.enabled_plugins ?? snapshot.plugins.map((plugin) => plugin.name),
    );
    if (enabled) current.add(name);
    else current.delete(name);
    try {
      await api.patchSettings({ enabled_plugins: [...current].sort() });
      await reload();
      toast.success(enabled ? `已启用 ${name}` : `已停用 ${name}`);
    } catch (error) {
      toast.error(`操作失败：${String(error)}`);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <GitBranchIcon className="size-3.5" />
            工作流
          </span>
          <Button variant="outline" size="sm" onClick={reloadPlugins} disabled={reloading}>
            {reloading ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RefreshCwIcon className="size-3.5" />
            )}
            重载插件
          </Button>
        </div>
        {listing && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
            <InfoIcon className="size-3 shrink-0" />
            <span className="min-w-0 flex-1">
              {listing.active_agent_workflow ? (
                <>
                  预设「{listing.active_agent}」固定为{" "}
                  <code>{listing.active_agent_workflow}</code>，下面的切换对它无效
                </>
              ) : (
                <>
                  预设「{listing.active_agent}」跟随全局，实际运行{" "}
                  <code>{listing.effective}</code>
                </>
              )}
            </span>
            <InfoHint label="工作流说明" align="end">
              <span className="flex flex-col gap-1.5">
                <span>
                  工作流由 Agent 预设决定：预设写了 <code>workflow</code> 就锁死，
                  没写才跟随这里。所以这里只对「没有固定工作流」的预设生效。
                </span>
                <span>
                  <span className="text-foreground/80">标准 ReAct</span>
                  ：一轮一次请求，模型自己决定调用哪些工具。
                </span>
                <span>
                  <span className="text-foreground/80">步进 ReAct</span>
                  ：先拆解成多步计划再逐步执行，开头多一次请求，但进度可见、更长任务更稳。
                </span>
              </span>
            </InfoHint>
          </div>
        )}

        {(listing?.workflows ?? []).map((workflow) => {
          const pinnedBy = listing?.pinned_by[workflow.name] ?? [];
          const pinnedHere =
            listing?.active_agent_workflow !== null &&
            listing?.active_agent_workflow === workflow.name;
          const locked =
            listing !== null &&
            listing.active_agent_workflow !== null &&
            listing.active_agent_workflow !== workflow.name;
          const selected =
            listing !== null &&
            (pinnedHere || listing.effective === workflow.name);
          return (
            <button
              key={workflow.name}
              type="button"
              disabled={locked}
              onClick={() => activateWorkflow(workflow.name)}
              title={
                locked
                  ? `当前预设固定了 ${listing?.active_agent_workflow}，切换不会生效`
                  : workflow.hint
              }
              className={cn(
                "flex items-start gap-2 rounded-lg border border-border px-3 py-2 text-left transition-colors",
                selected && "border-primary/50 bg-accent",
                locked ? "cursor-not-allowed opacity-60" : "hover:bg-accent",
              )}
            >
              <span className="mt-0.5">
                {pinnedHere ? (
                  <LockIcon className="size-3.5 text-primary" />
                ) : (
                  <GitBranchIcon className="size-3.5 text-muted-foreground" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2 text-sm">
                  {workflow.title}
                  <code className="text-[10px] text-muted-foreground">
                    {workflow.name}
                  </code>
                  <ModeBadge workflow={workflow} />
                  {pinnedHere && (
                    <span className="rounded bg-primary/10 px-1 py-0.5 text-[10px] text-primary">
                      预设指定
                    </span>
                  )}
                  {workflow.source !== "builtin" && (
                    <span className="rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
                      插件
                    </span>
                  )}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {workflow.description}
                </span>
                {workflow.hint && (
                  <span className="mt-0.5 block text-[11px] text-muted-foreground">
                    {workflow.hint}
                  </span>
                )}
                {pinnedBy.length > 0 && (
                  <span className="mt-0.5 block text-[11px] text-muted-foreground">
                    被预设固定：{pinnedBy.join("、")}
                  </span>
                )}
              </span>
            </button>
          );
        })}
        <p className="text-xs text-muted-foreground">
          预设在 <code>workflow</code> 字段中只能引用这里注册过的工作流；
          不写该字段则跟随全局选择。
        </p>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
        <div>
          <Label htmlFor="debug-mode" className="text-sm">
            开发者模式
          </Label>
          <p className="text-xs text-muted-foreground">
            每次激活时卸载再加载，验证插件没有副作用。
          </p>
        </div>
        <Switch
          id="debug-mode"
          checked={debug}
          title="开启后每次激活都卸载再重载插件，用来验证插件没有副作用；日常使用不必开"
          onCheckedChange={onDebugChange}
        />
      </div>

      <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border px-3 py-2">
        <div className="min-w-0">
          <span className="flex items-center gap-1">
            <Label htmlFor="mock-mode" className="text-sm">
              前端测试模式
            </Label>
            <MockHint />
          </span>
          <p className="text-xs text-muted-foreground">
            不发请求，用浏览器里的假回复跑完整个渲染链路（流式、思考、工具事件、用量）。
            刷新即消失，不会写进会话。
          </p>
        </div>
        <Switch
          id="mock-mode"
          checked={mock}
          title="本地伪造一条回复，不调用任何模型；用来检查渲染，不会写入会话"
          onCheckedChange={(value) => {
            setMockMode(value);
            setMock(value);
            toast.success(value ? "已开启前端测试模式" : "已关闭前端测试模式");
          }}
        />
      </div>

      {loading && !snapshot ? (
        <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
          <Loader2Icon className="mr-2 size-4 animate-spin" />
          加载中…
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <BoxesIcon className="size-3.5" />
              插件
            </span>
            {(snapshot?.plugins ?? []).map((plugin) => {
              const enabled = plugin.enabled !== false;
              return (
              <div
                key={plugin.name}
                className={cn(
                  "flex flex-col gap-1 rounded-lg border border-border px-3 py-2",
                  !enabled && "opacity-60",
                )}
              >
                <div className="flex items-center gap-2 text-sm">
                  <span className="truncate font-medium">{plugin.name}</span>
                  <code className="truncate text-[10px] text-muted-foreground">
                    {plugin.module}
                  </code>
                  {enabled && plugin.loaded === false && (
                    <span className="rounded bg-destructive/10 px-1 py-0.5 text-[10px] text-destructive">
                      未加载
                    </span>
                  )}
                  {plugin.has_config && enabled && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-auto h-6 px-2 text-[11px]"
                      title={`打开 ${plugin.name} 的插件配置`}
                      onClick={() =>
                        setOpenConfig((prev) =>
                          prev === plugin.name ? null : plugin.name,
                        )
                      }
                    >
                      <Settings2Icon className="size-3" />
                      {openConfig === plugin.name ? "收起" : "配置"}
                    </Button>
                  )}
                  {plugin.protected ? (
                    // Show protected plugins as locked instead of offering a rejected toggle.
                    <span
                      className={cn(
                        "flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground",
                        !(plugin.has_config && enabled) && "ml-auto",
                      )}
                      title="这个插件是本应用的基础能力，不能停用"
                    >
                      <LockIcon className="size-3" />
                      固定启用
                    </span>
                  ) : (
                    <Switch
                      // Align the switch to the row edge for consistent layout.
                      className={cn(!(plugin.has_config && enabled) && "ml-auto")}
                      checked={enabled}
                      aria-label={`启用 ${plugin.name}`}
                      title={
                        enabled
                          ? `停用 ${plugin.name}：它的工具、钩子与 MCP 提供方会消失`
                          : `启用 ${plugin.name}：重新加载它的工具、钩子与 MCP 提供方`
                      }
                      onCheckedChange={(value) => void togglePlugin(plugin.name, value)}
                    />
                  )}
                </div>
                {plugin.description && (
                  <p className="text-xs text-muted-foreground">{plugin.description}</p>
                )}
                <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                  {plugin.workflows.length > 0 && (
                    <span>工作流：{plugin.workflows.join(", ")}</span>
                  )}
                  {plugin.tools.length > 0 && <span>工具源：{plugin.tools.join(", ")}</span>}
                  {plugin.hooks.length > 0 && <span>钩子：{plugin.hooks.join(", ")}</span>}
                </div>
                {plugin.has_config && openConfig === plugin.name && enabled && (
                  <div className="mt-2 border-t border-border pt-3">
                    <PluginConfigForm plugin={plugin.name} />
                  </div>
                )}
              </div>
              );
            })}
            {(snapshot?.plugins ?? []).length === 0 && (
              <p className="text-xs text-muted-foreground">还没有注册插件</p>
            )}
            {snapshot?.directory && (
              <p className="text-xs text-muted-foreground">
                外部插件目录：<code className="break-all">{snapshot.directory}</code>
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              <WrenchIcon className="size-3.5" />
              已注册资源
            </span>
            <span>工具源 {(snapshot?.tools ?? []).length} 个</span>
            <span>MCP 提供者 {(snapshot?.mcp ?? []).length} 个</span>
            <span>生命周期钩子 {(snapshot?.hooks ?? []).join(", ") || "无"}</span>
          </div>
        </>
      )}
    </div>
  );
}
