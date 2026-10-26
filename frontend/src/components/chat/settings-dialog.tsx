import { useCallback, useEffect, useState } from "react";
import {
  CircleAlertIcon,
  CheckCircle2Icon,
  Loader2Icon,
  PlugZapIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";

import { AgentEditor } from "@/components/chat/agent-editor";
import { BehaviourPanel } from "@/components/chat/behaviour-panel";
import { CoreConfigEditor } from "@/components/chat/core-config-editor";
import { PluginPanel } from "@/components/chat/plugin-panel";
import { ProviderPanel } from "@/components/chat/provider-panel";
import { ToolPool } from "@/components/chat/tool-pool";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  api,
  type AgentPreset,
  type AppSettings,
  type MCPServer,
  type MCPSource,
  type ModelOption,
  type Provider,
  type Skill,
  type ToolCatalogEntry,
  type WorkflowSpec,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export type SettingsTab =
  | "providers"
  | "agents"
  | "behaviour"
  | "tools"
  | "core"
  | "skills"
  | "mcp"
  | "plugins";

/**
 * Tab bodies scroll, the dialog does not. Scrolling the whole dialog would drag
 * the header and tab bar out of view; the Agent tab opts out of this because it
 * scrolls its own two panes.
 */
const PANEL_SCROLL = "min-h-0 flex-1 overflow-y-auto pr-1";

export function SettingsDialog({
  open,
  onOpenChange,
  tab = "providers",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which tab to show when the dialog opens. */
  tab?: SettingsTab;
}) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(tab);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [activeModel, setActiveModel] = useState<string | null>(null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillDirs, setSkillDirs] = useState<string[]>([]);
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [agents, setAgents] = useState<AgentPreset[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowSpec[]>([]);
  const [toolCatalog, setToolCatalog] = useState<ToolCatalogEntry[]>([]);
  const [mcpSources, setMcpSources] = useState<MCPSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [mcpDraft, setMcpDraft] = useState({ name: "", transport: "" });

  // Select the requested tab on every dialog open.
  useEffect(() => {
    if (open) setActiveTab(tab);
  }, [open, tab]);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [loadedSettings, providerList, skillList, mcpList, agentList] =
        await Promise.all([
          api.getSettings(),
          api.listProviders(),
          api.listSkills(),
          api.listMcp(),
          api.listAgents(),
        ]);
      setSettings(loadedSettings);
      setProviders(providerList.providers);
      setModels(providerList.models);
      setActiveModel(providerList.active);
      setSkills(skillList.skills);
      setSkillDirs(skillList.directories);
      setServers(mcpList.servers);
      // Add the built-in fallback only when the agent list omits it.
      setAgents(
        agentList.agents.some((agent) => agent.name === agentList.builtin.name)
          ? agentList.agents
          : [agentList.builtin, ...agentList.agents],
      );
      setWorkflows(agentList.workflows);
      setToolCatalog(agentList.tools);
      setMcpSources(agentList.mcp);
    } catch (error) {
      toast.error(`加载设置失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  const removeServer = async (name: string) => {
    await api.deleteMcp(name);
    toast.success(`已删除 MCP ${name}`);
    await reload();
  };

  const toggleServer = async (name: string, enabled: boolean) => {
    try {
      await api.toggleMcp(name, enabled);
      await reload();
    } catch (error) {
      toast.error(`操作失败：${String(error)}`);
    }
  };

  const reconnectServer = async (name: string) => {
    try {
      const updated = await api.reconnectMcp(name);
      if (updated.error) toast.error(`${name}: ${updated.error}`);
      else toast.success(`${name} 已重连，${updated.tools.length} 个工具`);
      await reload();
    } catch (error) {
      toast.error(`重连失败：${String(error)}`);
    }
  };

  const addServer = async () => {
    if (!mcpDraft.name.trim() || !mcpDraft.transport.trim()) {
      toast.error("名称与传输地址不能为空");
      return;
    }
    try {
      const created = await api.saveMcp({
        name: mcpDraft.name.trim(),
        transport: mcpDraft.transport.trim(),
        enabled: true,
      });
      if (created.error) toast.error(created.error);
      else toast.success("MCP 服务器已添加并连接");
      setMcpDraft({ name: "", transport: "" });
      await reload();
    } catch (error) {
      toast.error(`添加失败：${String(error)}`);
    }
  };

  const toggleSkill = async (name: string, enabled: boolean) => {
    if (!settings) return;
    const current =
      settings.enabled_skills === null
        ? skills.map((skill) => skill.name)
        : [...settings.enabled_skills];
    const next = enabled
      ? [...current, name]
      : current.filter((item) => item !== name);
    const saved = await api.patchSettings({ enabled_skills: next });
    setSettings(saved);
  };

  const skillEnabled = (name: string) =>
    settings?.enabled_skills === null || settings?.enabled_skills.includes(name);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/*
        Full screen, not a floating card: these tabs hold editors (Markdown,
        forms) that need the room, and on a phone a centred 85vh dialog leaves
        nothing usable. It is a page you enter and leave.
      */}
      <DialogContent className="fixed inset-0 top-0 left-0 flex h-full max-h-none w-full max-w-none translate-x-0 translate-y-0 flex-col gap-4 overflow-hidden rounded-none border-0 p-4 sm:p-6">
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
          <DialogDescription>
            配置保存在 <code>~/.amrita</code>，API Key 加密存储；项目技能放在工作区的{" "}
            <code>.amrita/skills</code>。
          </DialogDescription>
        </DialogHeader>

        {loading && !settings ? (
          <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
            <Loader2Icon className="mr-2 size-4 animate-spin" />
            加载中…
          </div>
        ) : (
          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as SettingsTab)}
            className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col gap-4"
          >
            <TabsList
              className={cn(
                "w-full shrink-0",
                // Let tabs scroll on phones instead of squeezing their labels.
                "max-sm:h-auto max-sm:justify-start max-sm:overflow-x-auto",
                "max-sm:[&_[role=tab]]:flex-none max-sm:[&_[role=tab]]:px-3 max-sm:[&_[role=tab]]:py-2 max-sm:[&_[role=tab]]:text-[15px]",
              )}
            >
              <TabsTrigger value="providers">模型</TabsTrigger>
              <TabsTrigger value="agents">Agent</TabsTrigger>
              <TabsTrigger value="behaviour">行为</TabsTrigger>
              <TabsTrigger value="tools">工具</TabsTrigger>
              <TabsTrigger value="core">核心配置</TabsTrigger>
              <TabsTrigger value="skills">技能</TabsTrigger>
              <TabsTrigger value="mcp">MCP</TabsTrigger>
              <TabsTrigger value="plugins">插件</TabsTrigger>
            </TabsList>

            <TabsContent value="providers" className={PANEL_SCROLL}>
              <ProviderPanel
                providers={providers}
                models={models}
                active={activeModel}
                onReload={() => {
                  void reload();
                }}
              />
            </TabsContent>

            <TabsContent value="agents" className="min-h-0 flex-1 overflow-hidden">
              <AgentEditor
                agents={agents}
                workflows={workflows}
                tools={toolCatalog}
                mcp={mcpSources}
                onChanged={() => {
                  void reload();
                }}
              />
            </TabsContent>

            <TabsContent value="behaviour" className={PANEL_SCROLL}>
              {settings && (
                <BehaviourPanel
                  settings={settings}
                  onSaved={(saved) => setSettings(saved)}
                />
              )}
            </TabsContent>

            <TabsContent value="tools" className="flex min-h-0 flex-1 flex-col">              <ToolPool
                onSaved={() => {
                  void reload();
                }}
              />
            </TabsContent>

            <TabsContent value="core" className={PANEL_SCROLL}>
              <CoreConfigEditor />
            </TabsContent>

            <TabsContent value="skills" className={cn(PANEL_SCROLL, "flex flex-col gap-2")}>
              <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                <p>技能目录（工作区优先）：</p>
                {skillDirs.map((dir) => (
                  <code key={dir} className="break-all">
                    {dir}
                  </code>
                ))}
              </div>
              {skills.map((skill) => (
                <div
                  key={skill.name}
                  className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      {skill.name}
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {skill.scope === "workspace" ? "工作区" : "用户"}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {skill.description}
                    </div>
                  </div>
                  <Switch
                    checked={skillEnabled(skill.name)}
                    title={
                      skillEnabled(skill.name)
                        ? `关闭 ${skill.name}：它的说明不再注入到系统提示词`
                        : `启用 ${skill.name}：把它的说明注入到系统提示词`
                    }
                    onCheckedChange={(checked) => toggleSkill(skill.name, checked)}
                  />
                </div>
              ))}
              {skills.length === 0 && (
                <p className="text-sm text-muted-foreground">还没有发现技能</p>
              )}
            </TabsContent>

            <TabsContent value="mcp" className={cn(PANEL_SCROLL, "flex flex-col gap-3")}>
              {servers.map((server) => (
                <div
                  key={server.name}
                  className="flex flex-col gap-1 rounded-lg border border-border px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        {server.connected ? (
                          <CheckCircle2Icon className="size-3.5 text-emerald-500" />
                        ) : (
                          <CircleAlertIcon className="size-3.5 text-muted-foreground" />
                        )}
                        <span className="truncate">{server.name}</span>
                      </div>
                      <div className="truncate text-xs text-muted-foreground">
                        {server.transport}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Switch
                        checked={server.enabled}
                        title={
                          server.enabled
                            ? "停止这个 MCP 服务器，并回收它提供的工具"
                            : "连接这个 MCP 服务器，把它的工具加进工具池"
                        }
                        onCheckedChange={(checked) =>
                          toggleServer(server.name, checked)
                        }
                      />
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => reconnectServer(server.name)}
                        aria-label="重连"
                      >
                        <RefreshCwIcon className="size-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => removeServer(server.name)}
                        aria-label="删除"
                      >
                        <Trash2Icon className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                  {server.error && (
                    <p className="break-all text-xs text-destructive">{server.error}</p>
                  )}
                  {server.tools.length > 0 && (
                    <p className="text-xs text-muted-foreground">
                      工具：{server.tools.join(", ")}
                    </p>
                  )}
                </div>
              ))}

              <div className="grid grid-cols-1 gap-2 rounded-lg border border-dashed border-border p-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="mcp-name">名称</Label>
                  <Input
                    id="mcp-name"
                    value={mcpDraft.name}
                    onChange={(event) =>
                      setMcpDraft((prev) => ({ ...prev, name: event.target.value }))
                    }
                    placeholder="filesystem"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="mcp-transport">传输地址</Label>
                  <Input
                    id="mcp-transport"
                    value={mcpDraft.transport}
                    onChange={(event) =>
                      setMcpDraft((prev) => ({
                        ...prev,
                        transport: event.target.value,
                      }))
                    }
                    placeholder='stdio://["npx","-y","@modelcontextprotocol/server-filesystem","."]'
                  />
                </div>
                <Button onClick={addServer} className="justify-self-end">
                  <PlugZapIcon />
                  添加并连接
                </Button>
              </div>
            </TabsContent>

            <TabsContent value="plugins" className={PANEL_SCROLL}>
              <PluginPanel
                debug={settings?.debug ?? false}
                onDebugChange={async (value) => {
                  const saved = await api.patchSettings({ debug: value });
                  setSettings(saved);
                  toast.success(value ? "已开启开发者模式" : "已关闭开发者模式");
                }}
              />
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}
