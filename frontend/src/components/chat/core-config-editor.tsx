/**
 * AmritaCore runtime config editor.
 *
 * Fields are rendered from the schema the backend derives from
 * ``AmritaConfig``'s Pydantic model, so this component never needs a list of
 * options. The backend also returns a content hash; sending it back means a
 * concurrent edit surfaces as a 409 instead of silently overwriting someone
 * else's change — and validation errors come back per field, so they can be
 * shown next to the input that caused them.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDownIcon,
  Loader2Icon,
  RotateCcwIcon,
  SaveIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { toast } from "sonner";

import {
  ConfigField,
  errorFor,
  parseConfigError,
  type FieldError,
} from "@/components/chat/config-field";
import { Button } from "@/components/ui/button";
import { InfoHint } from "@/components/ui/info-hint";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, type CoreConfig, type CoreConfigField } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Group dotted paths by their first segment so the form has sections. */
function groupFields(fields: CoreConfigField[]) {
  const groups = new Map<string, CoreConfigField[]>();
  for (const field of fields) {
    const [head] = field.path.split(".");
    const bucket = groups.get(head) ?? [];
    bucket.push(field);
    groups.set(head, bucket);
  }
  return [...groups.entries()];
}

const GROUP_LABELS: Record<string, string> = {
  llm: "模型请求",
  function_config: "工具与函数调用",
  builtin: "内置 Agent",
  cookie: "安全",
};

/**
 * Shown above the Unsafe section.
 *
 * These options are not "advanced settings" by accident: each one changes what
 * the agent is willing to do or what it can still remember. Editing them is
 * legitimate (it is how you debug a loop that will not stop), but they are
 * pulled out of the normal groups so nobody changes one while looking for a
 * timeout.
 */
const UNSAFE_NOTE = {
  title: "这些选项会改变 Agent 的行为，不只是速度或费用",
  body: "改错通常不报错，只表现为只说不做、反复调同一个工具或忘掉前面的对话。不确定就留默认值。",
} as const;

/**
 * "Thinking" means two unrelated things here, and mixing them up is the usual
 * way people burn tokens or get mysterious provider 400s. The provider form
 * owns the native switch; everything in this section is prompt-level.
 */
const THINKING_NOTE = {
  title: "这里的思考是「非原生思考」",
  body: "只改提示词，不改请求参数，任何模型都能用。原生思考在「模型」页签按模型开关；两者同时开会让输出预算被分掉。",
} as const;

/**
 * The handful of knobs worth a real control.
 *
 * The generic form below is honest but joyless: a ratio is a float, a limit is
 * a bare number, and the only hint is the schema description. These rows are
 * the ones people actually tune, so they get a unit, a slider where the value
 * is a percentage, a switch where it is on/off, and one line saying what
 * breaks when it is set wrong. The paths are matched against the loaded
 * config, so a core version without one simply hides that row.
 */
type TuningKind = "number" | "percent" | "switch";

interface TuningSpec {
  path: string;
  label: string;
  hint: string;
  kind: TuningKind;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
}

const TUNING: TuningSpec[] = [
  {
    path: "function_config.agent_tool_call_limit",
    label: "工具调用轮数上限",
    hint: "一轮工具调用的总次数。调小会让多步骤任务在中途被硬性中断。",
    kind: "number",
    min: 1,
    max: 200,
    unit: "轮",
  },
  {
    path: "function_config.agent_tool_parallel_limit",
    label: "工具并行上限",
    hint: "同一轮里同时跑几个「安全」的工具调用。0 表示全部并行，1 表示串行；不安全的工具始终单独跑。",
    kind: "number",
    min: 0,
    max: 32,
    unit: "个",
  },
  {
    path: "llm.compaction_trigger_ratio",
    label: "压缩触发比例",
    hint: "上下文用到这个比例就开始折叠早期消息。设得太大，可能还没压缩就超限了。",
    kind: "percent",
  },
  {
    path: "llm.slide_target_ratio",
    label: "压缩后保留比例",
    hint: "折叠后保留多少上下文。设得太小，模型会丢掉任务前提。",
    kind: "percent",
  },
  {
    path: "llm.memory_length_limit",
    label: "历史消息条数上限",
    hint: "超过这个条数的历史不再进入请求，Agent 会「忘记」更早的对话。",
    kind: "number",
    min: 0,
    max: 2000,
    unit: "条",
  },
  {
    path: "function_config.use_minimal_context",
    label: "精简上下文",
    hint: "少发一部分历史，省钱，但 Agent 可能因此忘记之前的约定。",
    kind: "switch",
  },
  {
    path: "llm.enable_overflow_recovery",
    label: "超限自动恢复",
    hint: "上下文超限后自动压缩重试；关掉后超限直接失败。",
    kind: "switch",
  },
];

const TUNING_PATHS = new Set(TUNING.map((item) => item.path));

function CoreTuning({
  values,
  onChange,
  onSave,
  saving,
  dirty,
}: {
  values: Record<string, unknown>;
  onChange: (path: string, value: unknown) => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
}) {
  const rows = TUNING.filter((item) => values[item.path] !== undefined);
  if (rows.length === 0) return null;
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">常用设置</h3>
        <Button
          size="sm"
          variant="outline"
          disabled={saving || !dirty}
          onClick={onSave}
        >
          {saving ? <Loader2Icon className="animate-spin" /> : <SaveIcon />}
          保存
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {rows.map((item) => {
          const value = values[item.path];
          const id = `tune-${item.path.replace(/\./g, "-")}`;
          if (item.kind === "switch") {
            return (
              <div
                key={item.path}
                className="flex items-start justify-between gap-3 rounded-md border border-dashed border-border px-2.5 py-2"
              >
                <div className="min-w-0">
                  <div className="text-xs font-medium">{item.label}</div>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                    {item.hint}
                  </p>
                </div>
                <Switch
                  id={id}
                  checked={Boolean(value)}
                  onCheckedChange={(checked) => onChange(item.path, checked)}
                />
              </div>
            );
          }
          const numeric = Number(value);
          if (item.kind === "percent") {
            const percent = Math.round((Number.isFinite(numeric) ? numeric : 0) * 100);
            return (
              <div
                key={item.path}
                className="flex flex-col gap-1 rounded-md border border-dashed border-border px-2.5 py-2"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-medium">{item.label}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {percent}%
                  </span>
                </div>
                <input
                  id={id}
                  type="range"
                  min={10}
                  max={99}
                  step={1}
                  value={percent}
                  onChange={(event) =>
                    onChange(item.path, Number(event.target.value) / 100)
                  }
                  className="w-full accent-primary"
                />
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  {item.hint}
                </p>
              </div>
            );
          }
          return (
            <div
              key={item.path}
              className="flex flex-col gap-1 rounded-md border border-dashed border-border px-2.5 py-2"
            >
              <label htmlFor={id} className="text-xs font-medium">
                {item.label}
              </label>
              <div className="flex items-center gap-1.5">
                <Input
                  id={id}
                  type="number"
                  min={item.min}
                  max={item.max}
                  step={item.step ?? 1}
                  value={Number.isFinite(numeric) ? numeric : ""}
                  onChange={(event) =>
                    onChange(
                      item.path,
                      event.target.value === "" ? null : Number(event.target.value),
                    )
                  }
                  className="h-7 w-24 text-xs"
                />
                {item.unit && (
                  <span className="text-[11px] text-muted-foreground">
                    {item.unit}
                  </span>
                )}
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {item.hint}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function CoreConfigEditor() {
  const [config, setConfig] = useState<CoreConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [unsafeOpen, setUnsafeOpen] = useState(false);
  const [removedOpen, setRemovedOpen] = useState(false);
  const [pruning, setPruning] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await api.getCoreConfig();
      setConfig(loaded);
      setDraft(loaded.values);
      setErrors([]);
    } catch (error) {
      toast.error(`加载配置失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const dirty = useMemo(() => {
    if (!config) return false;
    return Object.entries(draft).some(
      ([path, value]) => config.values[path] !== value,
    );
  }, [config, draft]);

  const setValue = (path: string, value: unknown) => {
    setDraft((prev) => ({ ...prev, [path]: value }));
    setErrors((prev) => prev.filter((err) => err.field !== path));
  };

  const save = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const updated = await api.saveCoreConfig(draft, config.hash);
      setConfig({ ...config, values: updated.values, hash: updated.hash });
      setDraft(updated.values);
      setErrors([]);
      toast.success("配置已保存");
    } catch (error) {
      const parsed = parseConfigError(error);
      setErrors(parsed.errors);
      toast.error(parsed.message);
    } finally {
      setSaving(false);
    }
  };

  // Keep behavior-changing options separate from cost-tuning controls.
  const safeFields = useMemo(
    () =>
      (config?.fields ?? []).filter(
        (field) => !field.unsafe && !TUNING_PATHS.has(field.path),
      ),
    [config],
  );
  const unsafeFields = useMemo(
    () =>
      (config?.fields ?? []).filter(
        (field) => field.unsafe && !TUNING_PATHS.has(field.path),
      ),
    [config],
  );
  // The tuning rows are edited above, but they still count as unsafe: the
  // warning strip has to appear when one of them is what changed.
  const allUnsafe = useMemo(
    () => (config?.fields ?? []).filter((field) => field.unsafe),
    [config],
  );
  const unsafeTouched = useMemo(() => {
    if (!config) return 0;
    return allUnsafe.filter(
      (field) => config.values[field.path] !== draft[field.path],
    ).length;
  }, [config, draft, allUnsafe]);

  /** Keys the installed AmritaCore no longer knows about. */
  const removed = config?.removed ?? [];
  const prune = async () => {
    setPruning(true);
    try {
      const updated = await api.pruneCoreConfig();
      setConfig(updated);
      setDraft(updated.values);
      setErrors([]);
      toast.success("已清理不再使用的配置项");
    } catch (error) {
      toast.error(`清理失败：${String(error)}`);
    } finally {
      setPruning(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
        <Loader2Icon className="mr-2 size-4 animate-spin" />
        加载中…
      </div>
    );
  }

  if (!config) {
    return <p className="py-6 text-sm text-muted-foreground">无法读取配置</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">
        保存后写入 <code>~/.amrita/core.json</code>，重启后仍然生效。
      </p>

      <CoreTuning
        values={draft}
        onChange={setValue}
        onSave={() => void save()}
        saving={saving}
        dirty={dirty}
      />

      {removed.length > 0 && (
        <section className="flex flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <div className="flex items-start gap-2">
            <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">
                配置文件里有 {removed.length} 项当前版本已不再使用
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                多是 AmritaCore
                升级后删掉或改名的字段。它们不会生效，确认不需要后再清理。
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={prune}
              disabled={pruning}
            >
              {pruning ? <Loader2Icon className="animate-spin" /> : null}
              清理
            </Button>
          </div>
          <button
            type="button"
            onClick={() => setRemovedOpen((prev) => !prev)}
            aria-expanded={removedOpen}
            className="flex items-center gap-1 self-start text-[11px] text-muted-foreground hover:text-foreground"
          >
            <ChevronDownIcon
              className={cn(
                "size-3 transition-transform",
                removedOpen && "rotate-180",
              )}
            />
            {removedOpen ? "收起原始值" : "查看原始值"}
          </button>
          {removedOpen && (
            <pre className="max-h-48 overflow-auto rounded-md bg-muted/60 px-2 py-1.5 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
              {removed
                .map((item) => `${item.path} = ${JSON.stringify(item.value)}`)
                .join("\n")}
            </pre>
          )}
        </section>
      )}

      {(config.missing?.length ?? 0) > 0 && removed.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          有 {config.missing?.length}{" "}
          项没有写进配置文件，正在使用当前版本的默认值（下方标有 「默认」）。
        </p>
      )}

      {groupFields(safeFields).map(([group, fields]) => (
        <section key={group} className="flex flex-col gap-3">
          <h3 className="text-sm font-medium">
            {GROUP_LABELS[group] ?? group}
          </h3>
          <div className="flex flex-col gap-3">
            {fields.map((field) => (
              <ConfigField
                key={field.path}
                field={field}
                value={draft[field.path]}
                error={errorFor(errors, field.path)}
                onChange={(value) => setValue(field.path, value)}
                idPrefix="core"
              />
            ))}
          </div>
        </section>
      ))}

      {unsafeFields.length > 0 && (
        <section className="flex flex-col gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <div className="flex items-start gap-2">
            <button
              type="button"
              onClick={() => setUnsafeOpen((prev) => !prev)}
              className="flex min-w-0 flex-1 items-start gap-2 text-left"
              aria-expanded={unsafeOpen}
            >
              <TriangleAlertIcon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-sm font-medium">
                  Unsafe
                  <span className="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
                    {unsafeFields.length} 项
                  </span>
                  {unsafeTouched > 0 && (
                    <span className="text-[11px] font-normal text-amber-700 dark:text-amber-400">
                      已改动 {unsafeTouched} 项
                    </span>
                  )}
                </span>
                <span className="mt-1 block text-[11px] leading-relaxed text-muted-foreground">
                  改错不会报错，只会让 Agent 看起来变笨。
                </span>
              </span>
              <ChevronDownIcon
                className={cn(
                  "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform",
                  unsafeOpen && "rotate-180",
                )}
              />
            </button>
            {/* Outside the toggle: a button inside a button is invalid HTML and
                makes the hint click expand the section. */}
            <InfoHint label="Unsafe 选项说明" align="end" size="md">
              <span className="flex flex-col gap-1.5">
                <span className="font-medium text-foreground">
                  {UNSAFE_NOTE.title}
                </span>
                <span>{UNSAFE_NOTE.body}</span>
              </span>
            </InfoHint>
          </div>

          {unsafeOpen &&
            groupFields(unsafeFields).map(([group, fields]) => (
              <div
                key={group}
                className="flex flex-col gap-3 border-t border-amber-500/25 pt-3"
              >
                <h4 className="text-xs font-medium text-amber-700 dark:text-amber-400">
                  {GROUP_LABELS[group] ?? group}
                </h4>
                {group === "builtin" && (
                  <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    <span>{THINKING_NOTE.title}</span>
                    <InfoHint label="思考方式说明">
                      {THINKING_NOTE.body}
                    </InfoHint>
                  </div>
                )}
                {fields.map((field) => (
                  <ConfigField
                    key={field.path}
                    field={field}
                    value={draft[field.path]}
                    error={errorFor(errors, field.path)}
                    onChange={(value) => setValue(field.path, value)}
                    idPrefix="core-unsafe"
                  />
                ))}
              </div>
            ))}
        </section>
      )}

      <div className="flex items-center justify-between border-t border-border pt-3">
        <Button variant="ghost" size="sm" onClick={() => void reload()}>
          <RotateCcwIcon className="size-3.5" />
          重新载入
        </Button>
        <Button onClick={save} disabled={!dirty || saving}>
          {saving ? (
            <Loader2Icon className="animate-spin" />
          ) : (
            <SaveIcon className="size-4" />
          )}
          保存
        </Button>
      </div>
    </div>
  );
}
