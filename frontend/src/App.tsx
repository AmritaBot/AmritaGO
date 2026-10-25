import { useEffect, useMemo, useState } from "react";
import { MonitorIcon, MoonIcon, PlusIcon, PanelLeftIcon, SunIcon } from "lucide-react";
import { toast } from "sonner";

import { ChatView } from "@/components/chat/chat-view";
import { SessionSidebar } from "@/components/chat/session-sidebar";
import { SettingsDialog } from "@/components/chat/settings-dialog";
import type { SettingsTab } from "@/components/chat/settings-dialog";
import { Button } from "@/components/ui/button";
import { PromptDialog } from "@/components/ui/prompt-dialog";
import { useTheme } from "@/hooks/use-theme";
import { api, type AgentPreset, type ModelOption, type SessionMeta, type Workspace } from "@/lib/api";
import { cn } from "@/lib/utils";

const THEME_LABEL = {
  light: "浅色",
  dark: "深色",
  system: "跟随系统",
} as const;

const THEME_ICON = {
  light: SunIcon,
  dark: MoonIcon,
  system: MonitorIcon,
} as const;

export default function App() {
  const { theme, cycle } = useTheme();
  const ThemeIcon = THEME_ICON[theme];
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [archived, setArchived] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("providers");
  const [booting, setBooting] = useState(true);

  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string | null>(null);
  const [addWorkspaceOpen, setAddWorkspaceOpen] = useState(false);
  const [agents, setAgents] = useState<AgentPreset[]>([]);
  const [activeAgent, setActiveAgent] = useState("standard");
  const [models, setModels] = useState<ModelOption[]>([]);
  const [activeModel, setActiveModel] = useState<string | null>(null);

  const refreshSessions = async () => {
    const [live, stored] = await Promise.all([
      api.listSessions(),
      api.listSessions(true),
    ]);
    setSessions(live.sessions);
    setArchived(stored.sessions.filter((session) => session.archived));
    return live.sessions;
  };

  const refreshContext = async () => {
    const [workspaceList, agentList, providerList] = await Promise.all([
      api.listWorkspaces(),
      api.listAgents(),
      api.listProviders(),
    ]);
    setWorkspaces(workspaceList.workspaces);
    setActiveWorkspace(workspaceList.active ?? workspaceList.workspaces[0]?.path ?? null);
      // ``agents`` already carries the built-ins; only add the fallback entry
      // when it is missing, or the dropdown shows 标准模式 twice.
      setAgents(
        agentList.agents.some((agent) => agent.name === agentList.builtin.name)
          ? agentList.agents
          : [agentList.builtin, ...agentList.agents],
      );
    setActiveAgent(agentList.active ?? "standard");
    setModels(providerList.models);
    setActiveModel(
      providerList.active ?? providerList.models[0]?.key ?? null,
    );
  };

  useEffect(() => {
    (async () => {
      try {
        await refreshContext();
        const list = await refreshSessions();
        if (list.length > 0) {
          setActiveId(list[0].id);
        } else {
          const created = await api.createSession();
          setSessions([created]);
          setActiveId(created.id);
        }
      } catch (error) {
        console.error(error);
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  // The backend renames a session on first reply; keep the sidebar in sync.
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as {
        sessionId: string;
        title: string;
      };
      setSessions((prev) =>
        prev.map((session) =>
          session.id === detail.sessionId
            ? { ...session, title: detail.title }
            : session,
        ),
      );
    };
    window.addEventListener("amrita:session-title", handler);
    return () => window.removeEventListener("amrita:session-title", handler);
  }, []);

  // Token counters arrive with the run's `done` event.
  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as {
        sessionId: string | null;
        totals: SessionMeta["usage"];
      };
      if (!detail.sessionId) return;
      setSessions((prev) =>
        prev.map((session) =>
          session.id === detail.sessionId
            ? { ...session, usage: detail.totals, updated_at: Date.now() / 1000 }
            : session,
        ),
      );
    };
    window.addEventListener("amrita:usage", handler);
    return () => window.removeEventListener("amrita:usage", handler);
  }, []);

  const createSession = async () => {
    const created = await api.createSession();
    setSessions((prev) => [created, ...prev]);
    setActiveId(created.id);
    if (window.innerWidth < 900) setSidebarOpen(false);
  };

  const deleteSession = async (id: string) => {
    await api.deleteSession(id);
    setSessions((prev) => prev.filter((session) => session.id !== id));
    setArchived((prev) => prev.filter((session) => session.id !== id));
    if (activeId !== id) return;
    const remaining = sessions.filter((session) => session.id !== id);
    if (remaining.length > 0) {
      setActiveId(remaining[0].id);
      return;
    }
    const created = await api.createSession();
    setSessions([created]);
    setActiveId(created.id);
  };

  const renameSession = async (id: string, title: string) => {
    const updated = await api.renameSession(id, title);
    const apply = (list: SessionMeta[]) =>
      list.map((session) => (session.id === id ? updated : session));
    setSessions(apply);
    setArchived(apply);
  };

  const archiveSession = async (id: string, value: boolean) => {
    const updated = await api.archiveSession(id, value);
    if (value) {
      setSessions((prev) => prev.filter((session) => session.id !== id));
      setArchived((prev) => [updated, ...prev.filter((item) => item.id !== id)]);
      if (activeId === id) {
        const next = sessions.find((session) => session.id !== id);
        setActiveId(next?.id ?? null);
      }
    } else {
      setArchived((prev) => prev.filter((session) => session.id !== id));
      setSessions((prev) => [updated, ...prev]);
    }
  };

  const branchSession = async (source: SessionMeta) => {
    const created = await api.branchSession(source.id);
    setSessions((prev) => [created, ...prev]);
    setActiveId(created.id);
  };

  /**
   * Fork from a single bubble. The backend keeps every entry up to and
   * including it, which works for a user prompt too — consecutive user turns
   * are valid, so "say it differently" does not need a reply first.
   */
  const branchAt = async (entryId: string) => {
    if (!activeId) return;
    try {
      const created = await api.branchSession(activeId, { entry_id: entryId });
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
      toast.success("已从这条消息分叉");
    } catch (error) {
      toast.error(`分叉失败：${String(error).replace(/^Error:\s*/, "")}`);
    }
  };

  const active = useMemo(
    () =>
      [...sessions, ...archived].find((session) => session.id === activeId) ??
      null,
    [sessions, archived, activeId],
  );

  return (
    <div className="flex h-full overflow-hidden bg-background">
      <aside
        className={cn(
          "z-30 flex h-full w-[264px] shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[margin] duration-200",
          !sidebarOpen && "-ml-[264px]",
          "max-md:absolute max-md:shadow-xl",
        )}
      >
        <SessionSidebar
          sessions={sessions}
          archived={archived}
          activeId={activeId}
          onSelect={(id) => {
            setActiveId(id);
            if (window.innerWidth < 900) setSidebarOpen(false);
          }}
          onCreate={createSession}
          onDelete={deleteSession}
          onRename={renameSession}
          onArchive={archiveSession}
          onBranch={branchSession}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      </aside>

      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-3 max-sm:h-16 max-sm:gap-1.5">
          <Button
            variant="ghost"
            size="icon-sm"
            className="max-sm:size-10"
            onClick={() => setSidebarOpen((prev) => !prev)}
            aria-label="Toggle sidebar"
          >
            <PanelLeftIcon />
          </Button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-medium max-sm:text-base">
              {active?.title ?? "AmritaGO"}
            </h1>
            {active && (
              <p className="truncate text-xs text-muted-foreground">
                {active.agent ?? "标准模式"} · {active.message_count} 条消息
                {active.usage && active.usage.total_tokens > 0
                  ? ` · ${active.usage.total_tokens} tok`
                  : ""}
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            className="max-sm:size-10"
            onClick={cycle}
            title={`配色：${THEME_LABEL[theme]}（点击切换）`}
            aria-label={`配色：${THEME_LABEL[theme]}`}
          >
            <ThemeIcon />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="max-sm:h-10 max-sm:px-3"
            onClick={createSession}
          >
            <PlusIcon />
            新会话
          </Button>
        </header>

        {booting ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            正在启动…
          </div>
        ) : activeId ? (
          <ChatView
            key={activeId}
            sessionId={activeId}
            onTitleHint={(title) =>
              setSessions((prev) =>
                prev.map((session) =>
                  session.id === activeId ? { ...session, title } : session,
                ),
              )
            }
            workspaces={workspaces.map((item) => ({
              name: item.name,
              path: item.path,
            }))}
            activeWorkspace={activeWorkspace}
            agents={agents.map((agent) => ({
              name: agent.name,
              title: agent.title,
              description: agent.description,
              workflow: agent.workflow,
              workflowTitle: agent.workflow_title,
              workflowMode: agent.workflow_mode,
              workflowModeLabel: agent.workflow_mode_label,
              workflowPinned: agent.workflow_pinned,
              access: agent.access,
            }))}
            models={models}
            activeAgent={activeAgent}
            activeModel={activeModel}
            onSelectWorkspace={async (path) => {
              await api.activateWorkspace(path);
              setActiveWorkspace(path);
              await refreshContext();
            }}
            onAddWorkspace={() => setAddWorkspaceOpen(true)}
            onSelectAgent={async (name) => {
              await api.activateAgent(name);
              setActiveAgent(name);
            }}
            onSelectModel={async (key) => {
              await api.activateModel(key);
              setActiveModel(key);
            }}
            onClearSession={async () => {
              await api.clearSession(activeId);
            }}
            onOpenSettings={(tab) => {
              setSettingsTab(tab ?? "providers");
              setSettingsOpen(true);
            }}
            onBranch={(entryId) => {
              void branchAt(entryId);
            }}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            创建一个会话开始对话
          </div>
        )}
      </main>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        tab={settingsTab}
      />

      <PromptDialog
        open={addWorkspaceOpen}
        onOpenChange={setAddWorkspaceOpen}
        title="添加工作区"
        description="填一个本机的目录路径。加进来后会立刻切过去，之后的读写都在这个目录里。"
        label="工作区目录路径"
        placeholder="例如 C:\projects\my-app"
        confirmLabel="添加并切换"
        onSubmit={async (path) => {
          try {
            const entry = await api.addWorkspace(path);
            await api.activateWorkspace(entry.path);
            setActiveWorkspace(entry.path);
            await refreshContext();
            return null;
          } catch (error) {
            // The dialog stays open: a mistyped path is the common case, and
            // closing it would throw away what the user already typed.
            return String(error).replace(/^Error:\s*/, "");
          }
        }}
      />
    </div>
  );
}
