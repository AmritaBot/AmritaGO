/** Typed REST client for the AmritaGO backend. */

/** Coarse permission levels a preset may declare and the composer may ask for. */
export type AccessMode = "readonly" | "workspace" | "full";

export interface UsageTotals {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit: number;
  cache_creation: number;
  requests: number;
  turns: number;
  cost: number;
  currency: string;
}

export interface TurnUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit: number | null;
  cache_creation: number | null;
  requests: number;
  cost: number | null;
  currency: string;
  tokens_per_second: number | null;
  duration_ms: number | null;
  generation_ms: number | null;
  started_at: number | null;
  finished_at: number | null;
  context_tokens: number | null;
  max_context: number | null;
  context_percent: number | null;
  /** Messages the next request will carry (system prompt + tool results included). */
  context_messages?: number | null;
  rounds: number;
  steps: number;
  tool_calls: number;
  workflow: string | null;
  agent: string | null;
  preset: string | null;
  model: string | null;
  error: string | null;
}

export interface SessionMeta {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  preset: string | null;
  agent: string | null;
  /** Directory this conversation works in; ``null`` inherits the default. */
  workspace: string | null;
  message_count: number;
  usage: UsageTotals;
  archived: boolean;
  archived_at: number | null;
  parent_id: string | null;
  branch_from: string | null;
  branch_index: number | null;
  /**
   * A turn for this session is alive server-side right now.
   *
   * Computed per request (never stored), so a session keeps running while the
   * user looks at another one.
   */
  running?: boolean;
}

/**
 * One step of a turn, in the order it arrived.
 *
 * Thinking and tool calls alternate while the model works, so they are stored
 * as one timeline rather than as a reasoning blob plus an event list.
 */
export type TraceStep =
  | { kind: "reasoning"; text: string }
  | { kind: "event"; content: string; metadata: Record<string, unknown> };

export interface HistoryEntry {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: number;
  /** Thinking and tool steps interleaved, exactly as they happened. */
  trace?: TraceStep[];
  error?: string | null;
  usage?: TurnUsage | null;
  steps?: number;
  tool_calls?: number;
  workflow?: string | null;
  agent?: string | null;
  attachments?: Attachment[];
}

export interface AppSettings {
  active_model: string | null;
  active_agent: string | null;
  active_workflow: string | null;
  active_workspace: string | null;
  enabled_skills: string[] | null;
  enabled_mcp: string[];
  /** Tool name globs removed from every run's tool pool. */
  disabled_tools: string[];
  /** Authoritative plugin allow-list; names absent here are disabled. */
  enabled_plugins: string[] | null;
  /** Legacy inverted form, kept for older clients. */
  disabled_plugins: string[];
  debug: boolean;
  /** What a run does when it spends its tool-call budget. */
  tool_limit_policy: "graceful" | "ask";
  /** Rounds appended to the *same* turn each time the user answers "继续迭代". */
  tool_limit_extension: number;
  /** Wind-down rounds granted to a cut-off run (graceful close-out). */
  tool_limit_refusal_rounds: number;
  /** Seconds the parked run waits for an answer before winding down. */
  tool_limit_timeout: number;
  /** Wall-clock limit for one tool call, in seconds; `0` disables it. */
  tool_timeout: number;
  updated_at: number;
}

export interface ModelEntry {
  id: string;
  label: string;
  max_context: number | null;
  max_output: number;
  multimodal: boolean;
  temperature: number;
  top_p: number;
  thinking: boolean;
  /** `reasoning_effort` sent with each request; `null` keeps the default. */
  thinking_effort: string | null;
  price_in: number | null;
  price_out: number | null;
  currency: string;
}

export interface Provider {
  id: string;
  name: string;
  base_url: string;
  protocol: string;
  max_context: number | null;
  max_output: number;
  enabled: boolean;
  has_api_key: boolean;
  created_at: number;
  updated_at: number;
  models: ModelEntry[];
}

export interface ProviderPayload {
  id?: string | null;
  name: string;
  base_url: string;
  protocol: string;
  max_context: number | null;
  max_output: number;
  enabled: boolean;
  api_key?: string | null;
  models: ModelEntry[];
}

export interface ModelOption {
  key: string;
  provider_id: string;
  provider: string;
  model: string;
  label: string;
  multimodal: boolean;
  max_context: number | null;
  has_api_key: boolean;
  /** `reasoning_effort` this model sends; `null` keeps the default. */
  thinking_effort: string | null;
}

export interface ProbeResult {
  ok: boolean;
  models: string[];
  error: string | null;
  latency_ms: number | null;
}

export interface Attachment {
  id: string;
  kind: "image" | "text";
  name: string;
  mime: string;
  size: number;
  path: string | null;
  url?: string | null;
}

export interface PresetPayload {
  name: string;
  model: string;
  base_url: string;
  api_key?: string;
  protocol: string;
  max_context: number | null;
  max_output: number | null;
  temperature: number;
  top_p: number;
  stream: boolean;
  thinking: boolean;
}

export interface LegacyPreset {
  name: string;
  model: string;
  base_url: string;
  protocol: string;
  has_api_key: boolean;
}

export interface Skill {
  name: string;
  description: string;
  version: string | null;
  path: string;
  scope: "workspace" | "user";
}

export interface AgentPreset {
  name: string;
  title: string;
  description: string;
  icon: string;
  tools: string[] | null;
  access: AccessMode[] | null;
  read_only: boolean;
  /** ``null`` means "follow the globally active workflow". */
  workflow: string | null;
  workflow_pinned: boolean;
  workflow_title: string | null;
  workflow_mode: WorkflowMode | null;
  workflow_mode_label: string | null;
  /** ``"session"`` -> per-conversation scratch directory for plans/notes. */
  memory: "session" | null;
  scope: "builtin" | "user" | "workspace";
  /** This file overrides a built-in mode; deleting it restores the built-in. */
  shadows_builtin: boolean;
  path: string | null;
  has_custom_template: boolean;
  has_custom_prompt: boolean;
  /**
   * ``tools:`` entries that match nothing in the mixed pool (plugin tools +
   * MCP tools). Reported, never enforced: an MCP server may be offline.
   */
  unknown_tools?: string[];
}

export interface AgentDetail extends AgentPreset {
  raw: string;
  /** The preset body. Built-ins have no file, so this is the only copy. */
  prompt: string;
}

/** ``chat`` answers directly, ``react`` is the plain tool loop, ``step`` adds
 *  a decomposition pass and step lifecycle events. */
export type WorkflowMode = "chat" | "react" | "step";

export interface WorkflowSpec {
  name: string;
  title: string;
  description: string;
  source: string;
  mode: WorkflowMode;
  mode_label: string;
  hint: string;
  supports_steps: boolean;
}

export interface WorkflowListing {
  active: string | null;
  default: string;
  /** The workflow the active preset will actually run. */
  effective: string;
  active_agent: string;
  /** ``null`` when the preset follows the global switch. */
  active_agent_workflow: string | null;
  /** workflow name -> presets that pin it. */
  pinned_by: Record<string, string[]>;
  workflows: WorkflowSpec[];
}

export interface ToolCatalogEntry {
  name: string;
  description: string;
  source: string;
}

/** One tool in the pool, with every reason it might not run. */
export interface ToolNode {
  name: string;
  description: string;
  /** Plugin tool source name, or ``mcp:<server>``. */
  source: string;
  /** Kept by the global switches (this page). */
  enabled: boolean;
  /** Admitted by the active preset's ``tools`` whitelist. */
  preset_allowed: boolean;
  /** The active preset declares a whitelist at all. */
  preset_pinned: boolean;
  /** Allowed by the current access mode alone. */
  mode_allowed: boolean;
}

/** Tools grouped by the plugin or MCP server that supplies them. */
export interface ToolGroup {
  id: string;
  kind: "plugin" | "mcp";
  label: string;
  detail: string;
  plugin: string | null;
  connected: boolean;
  tools: ToolNode[];
  count: number;
  enabled_count: number;
}

export interface ToolTree {
  groups: ToolGroup[];
  total: number;
  enabled: number;
  disabled: string[];
  disabled_patterns: string[];
  access_mode: AccessMode;
  agent: string | null;
  preset_pinned: boolean;
}

export interface MCPSource {
  name: string;
  transport: string;
  enabled: boolean;
  connected: boolean;
  tools: string[];
}

export interface PluginInfo {
  name: string;
  description: string;
  module: string;
  tools: string[];
  workflows: string[];
  mcp: string[];
  hooks: string[];
  has_config: boolean;
  config_name: string | null;
  /** In the disabled list: registered but not activated. */
  enabled?: boolean;
  /** Contributed to the current snapshot (false when disabled or failed). */
  loaded?: boolean;
  /** The app depends on it, so the switch is refused by the backend. */
  protected?: boolean;
}

export interface PluginSnapshot {
  debug: boolean;
  directory: string;
  plugins: PluginInfo[];
  tools: { name: string; description: string }[];
  mcp: { name: string; description: string }[];
  workflows: WorkflowSpec[];
  hooks: string[];
  enabled_plugins: string[];
  disabled_plugins: string[];
  protected_plugins?: string[];
}

/** One live terminal session, as the shell plugin sees it. */
export interface ShellRun {
  command: string;
  exit_code: number | null;
  elapsed_ms: number;
  cwd: string;
  timed_out: boolean;
  at: number;
  output: string;
}

export interface ShellSession {
  id: string;
  /** Family: one POSIX implementation covers both bash and zsh. */
  shell: "pwsh" | "unix";
  /** Concrete interpreter: bash, zsh or pwsh. */
  flavour?: string;
  cwd: string;
  root: string;
  executable: string;
  executable_configured: boolean;
  /** Codec the output was decoded with. */
  encoding?: string;
  init_commands: string;
  started_at: number;
  runs: number;
  last: ShellRun | null;
  history: ShellRun[];
}

/** One item of the agent's plan for this conversation. */
export interface TodoItem {
  id: string;
  text: string;
  done: boolean;
}

export interface TodoList {
  title: string;
  items: TodoItem[];
  total: number;
  done: number;
}

/**
 * A slash command the backend intercepts before starting an agent turn.
 *
 * Built-ins and plugin-provided commands look the same here; `source` is only
 * there to explain where an unfamiliar command came from.
 */
export interface CommandInfo {
  name: string;
  title: string;
  description: string;
  /** Ready-to-type form, e.g. `/compact`. */
  usage: string;
  source: string;
}

/** A tool call waiting for the user's judgement. */
export interface ConfirmRequest {
  request_id: string;
  title: string;
  command: string;
  cwd: string;
  shell: string;
  hint: string;
  /** Tail of the previous run, shown read-only for context. */
  output: string;
}

/**
 * A run that spent its tool-call budget is asking whether to keep going.
 *
 * Answering ``continue`` appends `extension` rounds to **this turn**, so the
 * run picks up where it stopped; ``graceful`` winds it down and ``abort`` cuts
 * it off. A timeout or a closed tab is treated as ``graceful``.
 */
export interface ToolLimitRequest {
  request_id: string;
  /** Rounds the run was allowed before asking. */
  budget: number;
  /** Rounds "继续迭代" would append to this turn. */
  extension: number;
  /** Seconds until the question is treated as "graceful wind-down". */
  timeout: number;
}

/**
 * A tool call the server is executing right now.
 *
 * The window between this event and `tool_finished` is exactly when 取消 means
 * something, so the UI must stop offering it afterwards.
 */
export interface RunningTool {
  tool_id: string;
  tool: string;
  /** Seconds until the server gives up on this call; `0` means no limit. */
  timeout: number;
  started_at: number;
}

/** How a call ended, as reported by the server. */
export type ToolOutcome =
  | "done"
  | "failed"
  | "cancelled"
  | "timeout"
  | "aborted";

export interface Workspace {
  name: string;
  path: string;
  added_at: number;
}

export interface MCPServer {
  name: string;
  transport: string;
  enabled: boolean;
  connected: boolean;
  error: string | null;
  tools: string[];
}

export interface Usage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

/** One editable AmritaCore field, described by the backend's model schema. */
export interface CoreConfigField {
  path: string;
  label: string;
  type: "string" | "integer" | "number" | "boolean" | "literal";
  choices: string[] | null;
  description: string;
  doc?: string;
  default: unknown;
  constraints: {
    min?: number;
    max?: number;
    exclusive_min?: number;
    exclusive_max?: number;
  };
  /** Changes how the agent behaves, not just cost/latency — shown separately. */
  unsafe?: boolean;
  unsafe_reason?: string | null;
  /**
   * ``file`` when the saved config actually contains this key, ``default``
   * when the form is showing the library default because the key is absent
   * (fresh install, or a field added by a newer AmritaCore).
   */
  source?: "file" | "default";
}

export interface CoreConfigRemoved {
  path: string;
  value: unknown;
}

export interface CoreConfig {
  values: Record<string, unknown>;
  fields: CoreConfigField[];
  hash: string;
  /** Keys in the file that the installed AmritaCore no longer defines. */
  removed?: CoreConfigRemoved[];
  /** Editable paths that are absent from the file, i.e. using defaults. */
  missing?: string[];
}

/** One plugin's settings, described by the model the plugin declared. */
export interface PluginConfig {
  plugin: string;
  fields: CoreConfigField[];
  values: Record<string, unknown>;
  hash: string;
}

export interface PluginConfigList {
  configs: PluginConfig[];
  directory: string;
}

export type StreamEvent =
  | {
      type: "start";
      stream_id: string;
      session_id: string;
      agent: string;
      workflow: string;
      workflow_title: string;
      preset: string;
      model: string;
      tools: number;
    }
  | { type: "delta"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "meta"; content: string; metadata: Record<string, unknown> }
  | ({ type: "confirm" } & ConfirmRequest)
  // ``status`` says what ended the request: answered, timed_out or cancelled.
  | { type: "confirm_closed"; request_id: string; status?: string }
  | ({ type: "limit" } & ToolLimitRequest)
  | ({ type: "tool_running" } & RunningTool)
  | { type: "tool_finished"; tool_id: string; outcome: ToolOutcome }
  | { type: "todos"; todos: TodoList }
  | {
      type: "usage";
      usage: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
        requests: number;
      };
    }
  | { type: "error"; message: string }
  | {
      type: "done";
      usage: TurnUsage | null;
      totals: UsageTotals | null;
      title?: string | null;
      message_count?: number | null;
      workflow?: string;
      agent?: string;
    }
  | { type: "pong" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Turn a failed response into one readable line.
 *
 * The backend answers with FastAPI's ``{"detail": "…"}`` envelope, so a naive
 * ``response.text()`` put JSON — complete with escaped quotes and doubled
 * backslashes — in front of the user. ``detail`` is also the only place the
 * actual reason lives ("目录不存在：…"), while the status code says nothing.
 */
async function errorMessage(response: Response): Promise<string> {
  const body = await response.text();
  const fallback = `${response.status} ${response.statusText}`;
  if (!body) return fallback;
  try {
    const parsed = JSON.parse(body) as { detail?: unknown; error?: unknown };
    const detail = parsed.detail ?? parsed.error;
    if (typeof detail === "string" && detail.trim()) return detail;
    // Format validation errors as concise, readable lines.
    if (Array.isArray(detail)) {
      const lines = detail
        .map((item) => (item as { msg?: string }).msg)
        .filter((msg): msg is string => typeof msg === "string");
      if (lines.length > 0) return lines.join("；");
    }
  } catch {
    // Not JSON (a proxy error page, say): the body itself is the message.
  }
  return body.slice(0, 400) || fallback;
}

export const api = {
  // meta
  health: () => request<{ ok: boolean; version: string }>("/health"),

  // settings
  getSettings: () => request<AppSettings>("/settings"),
  patchSettings: (patch: Partial<AppSettings>) =>
    request<AppSettings>("/settings", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  // Presets, providers, and models.
  listProviders: () =>
    request<{
      active: string | null;
      providers: Provider[];
      models: ModelOption[];
    }>("/providers"),
  saveProvider: (provider: ProviderPayload) =>
    request<Provider>("/providers", {
      method: "POST",
      body: JSON.stringify(provider),
    }),
  deleteProvider: (id: string) =>
    request<{ ok: boolean }>(`/providers/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  toggleProvider: (id: string, enabled: boolean) =>
    request<Provider>(
      `/providers/${encodeURIComponent(id)}/toggle?enabled=${enabled}`,
      { method: "POST" },
    ),
  addProviderModel: (id: string, model: ModelEntry) =>
    request<Provider>(`/providers/${encodeURIComponent(id)}/models`, {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  updateProviderModel: (id: string, model: ModelEntry) =>
    request<Provider>(`/providers/${encodeURIComponent(id)}/models`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  removeProviderModel: (id: string, modelId: string) =>
    request<Provider>(
      `/providers/${encodeURIComponent(id)}/models/${encodeURIComponent(modelId)}`,
      { method: "DELETE" },
    ),
  /** Change one knob on a model without resending the whole entry. */
  patchModel: (
    providerId: string,
    modelId: string,
    patch: { thinking_effort?: string | null; thinking?: boolean },
  ) =>
    request<Provider>(
      `/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),
  probeProvider: (base_url: string, api_key: string, protocol: string) =>
    request<ProbeResult>("/providers/probe", {
      method: "POST",
      body: JSON.stringify({ base_url, api_key, protocol }),
    }),
  activateModel: (key: string) =>
    request<{ active: string; model: string; provider: string }>(
      "/models/activate",
      { method: "POST", body: JSON.stringify({ key }) },
    ),

  // attachments
  uploadAttachment: (payload: {
    kind: "image" | "text";
    name?: string;
    data_url?: string;
    text?: string;
  }) =>
    request<Attachment>("/attachments", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // legacy presets (read-only)
  listLegacyPresets: () => request<{ presets: LegacyPreset[] }>("/presets"),

  // skills
  listSkills: () =>
    request<{
      enabled: string[] | null;
      directories: string[];
      skills: Skill[];
    }>("/skills"),

  // agent presets
  listAgents: () =>
    request<{
      active: string | null;
      directories: string[];
      agents: AgentPreset[];
      builtin: AgentPreset;
      workflows: WorkflowSpec[];
      tools: ToolCatalogEntry[];
      mcp: MCPSource[];
    }>("/agents"),
  getAgent: (name: string) =>
    request<AgentDetail>(`/agents/${encodeURIComponent(name)}`),
  saveAgent: (name: string, raw: string, workspace?: string) =>
    request<AgentPreset>(`/agents/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ raw, workspace }),
    }),
  createAgent: (name: string, title: string, scope: "user" | "workspace") =>
    request<AgentPreset>("/agents", {
      method: "POST",
      body: JSON.stringify({ name, title, scope }),
    }),
  deleteAgent: (name: string) =>
    request<{ ok: boolean; restored_builtin?: boolean }>(
      `/agents/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  activateAgent: (name: string) =>
    request<{ active: string }>(
      `/agents/${encodeURIComponent(name)}/activate`,
      {
        method: "POST",
      },
    ),

  // workflows
  listWorkflows: () => request<WorkflowListing>("/workflows"),
  activateWorkflow: (name: string) =>
    request<{ active: string }>("/workflows/activate", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  // plugins and capabilities
  listPlugins: () => request<PluginSnapshot>("/plugins"),
  shells: () =>
    request<{
      available: string[];
      sessions: ShellSession[];
      spool: string;
    }>("/shells"),
  closeShell: (sessionId: string) =>
    request<{ ok: boolean }>(`/shells/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
  toolTree: (agent?: string) =>
    request<ToolTree>(
      `/tools${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`,
    ),
  reloadPlugins: () =>
    request<PluginSnapshot>("/plugins/reload", { method: "POST" }),
  capabilities: (agent?: string) =>
    request<{
      agent: string;
      workflow: string;
      workflow_mode: WorkflowMode;
      workflow_mode_label: string;
      workflow_pinned: boolean;
      tools: ToolCatalogEntry[];
      mcp: MCPSource[];
      workflows: WorkflowSpec[];
      plugins: PluginInfo[];
    }>(`/capabilities${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`),

  // workspaces
  listWorkspaces: () =>
    request<{ active: string | null; workspaces: Workspace[] }>("/workspaces"),
  addWorkspace: (path: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  activateWorkspace: (path: string) =>
    request<Workspace>("/workspaces/activate", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  removeWorkspace: (path: string) =>
    request<{ ok: boolean }>(`/workspaces?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    }),

  // AmritaCore runtime config
  getCoreConfig: () => request<CoreConfig>("/core-config"),
  saveCoreConfig: (values: Record<string, unknown>, hash: string) =>
    request<{ values: Record<string, unknown>; hash: string }>("/core-config", {
      method: "PUT",
      body: JSON.stringify({ values, hash }),
    }),
  /** Drop keys the installed AmritaCore no longer defines. */
  pruneCoreConfig: () =>
    request<CoreConfig>("/core-config/prune", { method: "POST" }),

  // conversation plan
  getTodos: (sessionId: string) =>
    request<TodoList>(`/sessions/${encodeURIComponent(sessionId)}/todos`),
  clearTodos: (sessionId: string) =>
    request<{ ok: boolean }>(
      `/sessions/${encodeURIComponent(sessionId)}/todos`,
      { method: "DELETE" },
    ),

  // plugin configuration (schema derived from each plugin's Pydantic model)
  listPluginConfigs: () => request<PluginConfigList>("/plugin-configs"),
  getPluginConfig: (plugin: string) =>
    request<PluginConfig>(`/plugin-configs/${encodeURIComponent(plugin)}`),
  savePluginConfig: (
    plugin: string,
    values: Record<string, unknown>,
    hash: string,
  ) =>
    request<PluginConfig>(`/plugin-configs/${encodeURIComponent(plugin)}`, {
      method: "PUT",
      body: JSON.stringify({ values, hash }),
    }),

  // mcp
  listMcp: () => request<{ servers: MCPServer[] }>("/mcp"),
  saveMcp: (server: { name: string; transport: string; enabled: boolean }) =>
    request<MCPServer>("/mcp", {
      method: "POST",
      body: JSON.stringify(server),
    }),
  deleteMcp: (name: string) =>
    request<{ ok: boolean }>(`/mcp/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  toggleMcp: (name: string, enabled: boolean) =>
    request<MCPServer>(
      `/mcp/${encodeURIComponent(name)}/toggle?enabled=${enabled}`,
      { method: "POST" },
    ),
  reconnectMcp: (name: string) =>
    request<MCPServer>(`/mcp/${encodeURIComponent(name)}/reconnect`, {
      method: "POST",
    }),

  // sessions
  listCommands: () => request<{ commands: CommandInfo[] }>("/commands"),
  listSessions: (includeArchived = false) =>
    request<{ sessions: SessionMeta[] }>(
      `/sessions?include_archived=${includeArchived}`,
    ),
  createSession: (title = "新会话") =>
    request<SessionMeta>("/sessions", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),
  renameSession: (id: string, title: string) =>
    request<SessionMeta>(`/sessions/${id}?title=${encodeURIComponent(title)}`, {
      method: "PATCH",
    }),
  /**
   * Point one conversation at a workspace and/or agent.
   *
   * ``undefined`` leaves a field alone, so the two switchers can update the
   * session independently of each other.
   */
  setSessionConfig: (
    id: string,
    config: { workspace?: string; agent?: string },
  ) =>
    request<SessionMeta>(`/sessions/${id}/config`, {
      method: "POST",
      body: JSON.stringify(config),
    }),
  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/sessions/${id}`, { method: "DELETE" }),
  archiveSession: (id: string, archived = true) =>
    request<SessionMeta>(`/sessions/${id}/archive?archived=${archived}`, {
      method: "POST",
    }),
  branchSession: (
    id: string,
    options?: { index?: number; entry_id?: string; title?: string },
  ) =>
    request<SessionMeta>(`/sessions/${id}/branch`, {
      method: "POST",
      body: JSON.stringify(options ?? {}),
    }),
  listBranches: (id: string) =>
    request<{ branches: SessionMeta[] }>(`/sessions/${id}/branches`),
  history: (id: string) =>
    request<{ entries: HistoryEntry[] }>(`/sessions/${id}/history`),
  /**
   * Rewrite the last prompt the agent never answered.
   *
   * The server rolls the context back to before it and drops the failed
   * exchange; the caller then sends the edited text as a normal turn.
   */
  editMessage: (id: string, payload: { content: string; expected?: string }) =>
    request<{
      ok: boolean;
      entry_id: string;
      previous: string;
      content: string;
      removed_entries: number;
      removed_messages: number;
      context_messages: number;
    }>(`/sessions/${id}/messages/edit`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  usage: (id: string) =>
    request<{
      totals: UsageTotals;
      turns: TurnUsage[];
      /** Size of the context the model will actually be sent. */
      context: { messages: number; abstract: boolean; tokens: number | null };
    }>(`/sessions/${id}/usage`),
  clearSession: (id: string) =>
    request<{ ok: boolean }>(`/sessions/${id}/clear`, { method: "POST" }),
  /** Fold older history into the summary now, instead of waiting for the trigger. */
  compactSession: (id: string) =>
    request<{
      compacted: boolean;
      folded: number;
      kept: number;
      summary_chars: number;
      messages: number;
      detail: string;
    }>(`/sessions/${id}/compact`, { method: "POST" }),

  // shells
  listShells: () =>
    request<{
      available: string[];
      sessions: { shell: string; cwd: string }[];
    }>("/shells"),

  // streams
  streamState: (sessionId: string) =>
    request<{
      active: boolean;
      stream_id?: string;
      bytes?: number;
      error?: string | null;
    }>(`/streams/${sessionId}`),
};

export function wsUrl(sessionId?: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  return `${proto}//${location.host}/api/ws/chat${query}`;
}
