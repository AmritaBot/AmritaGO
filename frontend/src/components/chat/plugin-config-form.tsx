/**
 * Per-plugin settings, rendered from the schema each plugin declared.
 *
 * A plugin never ships a form: it declares a Pydantic model, the backend turns
 * that model into field descriptors, and this component draws them. The same
 * content hash round-trip as the AmritaCore editor applies, so two open tabs
 * cannot silently overwrite each other.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2Icon, RotateCcwIcon, SaveIcon } from "lucide-react";
import { toast } from "sonner";

import {
  ConfigField,
  errorFor,
  parseConfigError,
  type FieldError,
} from "@/components/chat/config-field";
import { Button } from "@/components/ui/button";
import { api, type PluginConfig } from "@/lib/api";

export function PluginConfigForm({ plugin }: { plugin: string }) {
  const [config, setConfig] = useState<PluginConfig | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const loaded = await api.getPluginConfig(plugin);
      setConfig(loaded);
      setDraft(loaded.values);
      setErrors([]);
    } catch (error) {
      toast.error(`加载 ${plugin} 的配置失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  }, [plugin]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const dirty = useMemo(() => {
    if (!config) return false;
    return Object.entries(draft).some(([path, value]) => config.values[path] !== value);
  }, [config, draft]);

  const setValue = (path: string, value: unknown) => {
    setDraft((prev) => ({ ...prev, [path]: value }));
    setErrors((prev) => prev.filter((err) => err.field !== path));
  };

  const save = async () => {
    if (!config) return;
    setSaving(true);
    try {
      const updated = await api.savePluginConfig(plugin, draft, config.hash);
      setConfig(updated);
      setDraft(updated.values);
      setErrors([]);
      toast.success(`${plugin} 的配置已保存`);
    } catch (error) {
      const parsed = parseConfigError(error);
      setErrors(parsed.errors);
      toast.error(parsed.message);
      // A stale hash means someone else wrote first; reloading shows what they
      // saved and gives the user a chance to re-apply their edit deliberately.
      if (parsed.errors.length === 0) void reload();
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-xs text-muted-foreground">
        <Loader2Icon className="mr-2 size-3.5 animate-spin" />
        加载中…
      </div>
    );
  }

  if (!config) {
    return <p className="py-4 text-xs text-muted-foreground">无法读取该插件的配置</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        由插件声明的 Pydantic 模型生成，保存后写入{" "}
        <code>~/.amrita/plugin_configs/{plugin}.json</code>。
      </p>

      <div className="flex flex-col gap-3">
        {config.fields.map((field) => (
          <ConfigField
            key={field.path}
            field={field}
            value={draft[field.path]}
            error={errorFor(errors, field.path)}
            onChange={(value) => setValue(field.path, value)}
            idPrefix={`plugin-${plugin}`}
          />
        ))}
      </div>

      <div className="flex items-center justify-between border-t border-border pt-3">
        <Button variant="ghost" size="sm" onClick={() => void reload()}>
          <RotateCcwIcon className="size-3.5" />
          重新载入
        </Button>
        <Button size="sm" onClick={save} disabled={!dirty || saving}>
          {saving ? (
            <Loader2Icon className="size-3.5 animate-spin" />
          ) : (
            <SaveIcon className="size-3.5" />
          )}
          保存
        </Button>
      </div>
    </div>
  );
}
