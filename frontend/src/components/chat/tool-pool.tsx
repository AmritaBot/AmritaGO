/**
 * The tool pool, grouped by where each tool comes from.
 *
 * Every conversation gets a fresh ``MultiToolsManager`` composed on the server
 * from the active plugin snapshot, so "disable a tool" is a filter applied to
 * that copy — nothing global is mutated. The tree shows the full catalog (so
 * an agent preset's whitelist can be written against it) and marks each tool
 * with every reason it might still not run.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BoxesIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronsDownUpIcon,
  ChevronsUpDownIcon,
  FileTextIcon,
  ListTodoIcon,
  NotebookPenIcon,
  PlugIcon,
  SearchIcon,
  SparklesIcon,
  SquareTerminalIcon,
  WrenchIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { InfoHint } from "@/components/ui/info-hint";
import { Input } from "@/components/ui/input";
import { api, type ToolGroup, type ToolNode, type ToolTree } from "@/lib/api";
import { cn } from "@/lib/utils";

const GROUP_ICONS: Record<string, typeof WrenchIcon> = {
  core: BoxesIcon,
  skills: SparklesIcon,
  memory: NotebookPenIcon,
  files: FileTextIcon,
  todos: ListTodoIcon,
  pwsh: SquareTerminalIcon,
  unix: SquareTerminalIcon,
};

function groupIcon(group: ToolGroup) {
  if (group.kind === "mcp") return PlugIcon;
  // ``id`` is ``plugin:<tool source>``; ``label`` is the human translation.
  const source = group.id.replace(/^plugin:/, "");
  return GROUP_ICONS[source] ?? BoxesIcon;
}

function nodeState(tool: ToolNode): "on" | "off" | "blocked" {
  if (!tool.enabled) return "off";
  if (!tool.preset_allowed || !tool.mode_allowed) return "blocked";
  return "on";
}

export function ToolPool({
  onSaved,
}: {
  /** Called after a successful switch so the caller can refresh shared state. */
  onSaved?: () => void;
}) {
  const [tree, setTree] = useState<ToolTree | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      setTree(await api.toolTree());
    } catch (error) {
      toast.error(`加载工具失败：${String(error)}`);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const groups = useMemo(() => {
    if (!tree) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return tree.groups;
    return tree.groups
      .map((group) => {
        const tools = group.tools.filter(
          (tool) =>
            tool.name.toLowerCase().includes(needle) ||
            tool.description.toLowerCase().includes(needle) ||
            group.label.toLowerCase().includes(needle),
        );
        return { ...group, tools };
      })
      .filter((group) => group.tools.length > 0);
  }, [tree, query]);

  /**
   * Persist a new disabled-tool set.
   *
   * The value on disk may be globs (``pwsh_*``) from a hand-edited settings
   * file; the first toggle through this UI expands them into explicit names,
   * because "everything under this glob except one tool" has no glob spelling.
   * The result is always the same effective set the user is looking at.
   */
  const persist = async (disabled: Set<string>) => {
    if (!tree) return;
    const next = [...disabled].sort();
    const optimistic: ToolTree = {
      ...tree,
      groups: tree.groups.map((group) => {
        const tools = group.tools.map((tool) => ({
          ...tool,
          enabled: !next.includes(tool.name),
        }));
        return {
          ...group,
          tools,
          enabled_count: tools.filter((tool) => tool.enabled).length,
        };
      }),
      disabled: next,
      disabled_patterns: next,
      enabled: tree.groups
        .flatMap((group) => group.tools)
        .filter((tool) => !next.includes(tool.name) && tool.mode_allowed).length,
    };
    setTree(optimistic);
    setBusy(true);
    try {
      await api.patchSettings({ disabled_tools: next });
      onSaved?.();
    } catch (error) {
      toast.error(`保存失败：${String(error)}`);
      await reload();
    } finally {
      setBusy(false);
    }
  };

  const disabledSet = () =>
    new Set(
      (tree?.groups ?? []).flatMap((group) =>
        group.tools.filter((tool) => !tool.enabled).map((tool) => tool.name),
      ),
    );

  const toggleTool = (tool: ToolNode, enabled: boolean) => {
    const next = disabledSet();
    if (enabled) next.delete(tool.name);
    else next.add(tool.name);
    void persist(next);
  };

  const toggleGroup = (group: ToolGroup, enabled: boolean) => {
    const next = disabledSet();
    for (const tool of group.tools) {
      if (enabled) next.delete(tool.name);
      else next.add(tool.name);
    }
    void persist(next);
  };

  const setAllCollapsed = (value: boolean) => {
    setCollapsed(value ? new Set((tree?.groups ?? []).map((g) => g.id)) : new Set());
  };

  if (!tree) {
    return <p className="text-sm text-muted-foreground">加载中…</p>;
  }

  const toggleCollapse = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索工具名或说明"
            className="pl-8"
            aria-label="搜索工具"
          />
        </div>
        <span className="shrink-0 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground">
          已启用 <b className="text-foreground">{tree.enabled}</b> / {tree.total}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={collapsed.size ? "全部展开" : "全部折叠"}
          onClick={() => setAllCollapsed(collapsed.size === 0)}
        >
          {collapsed.size ? (
            <ChevronsUpDownIcon className="size-4" />
          ) : (
            <ChevronsDownUpIcon className="size-4" />
          )}
        </Button>
      </div>

      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
        开关对所有会话生效，每次对话按快照重新组池。
        {tree.preset_pinned && tree.agent && (
          <span>
            当前预设「{tree.agent}」带工具白名单，只勾这里的工具还不够。
          </span>
        )}
        <InfoHint label="工具开关说明">
          <span className="flex flex-col gap-1.5">
            <span>
              工具池是每次对话临时组装的：插件声明的工具、MCP 服务器提供的工具都在里面，
              这里的开关只是在装配时把它们滤掉，不会改动插件本身。
            </span>
            <span>
              <span className="text-foreground/80">灰掉的工具</span>
              ：勾着也不会跑。Agent 预设写了 <code>tools:</code>{" "}
              白名单却没收它，或者当前访问模式不允许。
            </span>
            <span>
              只读模式下 shell 工具仍会保留，写入由命令检查拦；想彻底不跑就别勾它。
            </span>
          </span>
        </InfoHint>
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-border">
        {groups.length === 0 && (
          <p className="p-4 text-sm text-muted-foreground">
            {query ? "没有匹配的工具" : "还没有发现工具"}
          </p>
        )}
        {groups.map((group) => {
          const Icon = groupIcon(group);
          const isCollapsed = collapsed.has(group.id);
          const allOn = group.tools.every((tool) => tool.enabled);
          const noneOn = group.tools.every((tool) => !tool.enabled);
          return (
            <div key={group.id} className="border-b border-border last:border-b-0">
              <div className="flex items-center gap-2 px-2 py-1.5 max-sm:py-2.5">
                <button
                  type="button"
                  onClick={() => toggleCollapse(group.id)}
                  aria-expanded={!isCollapsed}
                  aria-label={isCollapsed ? `展开 ${group.label}` : `折叠 ${group.label}`}
                  className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  {isCollapsed ? (
                    <ChevronRightIcon className="size-4" />
                  ) : (
                    <ChevronDownIcon className="size-4" />
                  )}
                </button>
                <Checkbox
                  checked={allOn}
                  indeterminate={!allOn && !noneOn}
                  onChange={(event) => toggleGroup(group, event.target.checked)}
                  aria-label={`启用 ${group.label} 的全部工具`}
                />
                <Icon className="size-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">
                  {group.label}
                  {group.detail && (
                    <span className="ml-2 truncate text-xs font-normal text-muted-foreground">
                      {group.detail}
                    </span>
                  )}
                </span>
                {group.kind === "mcp" && !group.connected && (
                  <span className="shrink-0 text-[10px] text-destructive">未连接</span>
                )}
                <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                  {group.enabled_count}/{group.count}
                </span>
              </div>

              {!isCollapsed && (
                <ul className="pb-1">
                  {group.tools.map((tool) => {
                    const state = nodeState(tool);
                    const reason = !tool.enabled
                      ? null
                      : !tool.mode_allowed
                        ? `当前访问模式（${tree.access_mode}）不允许`
                        : !tool.preset_allowed
                          ? `预设「${tree.agent}」的白名单没有它`
                          : null;
                    return (
                      <li key={tool.name}>
                        <label
                          className={cn(
                            "flex cursor-pointer items-start gap-2 py-1 pr-3 pl-9 hover:bg-muted/40 max-sm:py-2",
                            state === "off" && "opacity-60",
                          )}
                        >
                          <Checkbox
                            className="mt-0.5"
                            checked={tool.enabled}
                            disabled={busy}
                            aria-label={`启用 ${tool.name}`}
                            onChange={(event) => toggleTool(tool, event.target.checked)}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-baseline gap-x-2">
                              <code
                                className={cn(
                                  "text-xs",
                                  state === "blocked" && "text-muted-foreground",
                                )}
                              >
                                {tool.name}
                              </code>
                              {reason && (
                                <span className="text-[10px] text-muted-foreground">
                                  {reason}
                                </span>
                              )}
                            </span>
                            {tool.description && (
                              <span
                                className="block truncate text-[11px] text-muted-foreground"
                                title={tool.description}
                              >
                                {tool.description}
                              </span>
                            )}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
