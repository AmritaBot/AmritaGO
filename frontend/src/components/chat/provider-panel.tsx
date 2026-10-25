/**
 * Provider and model management.
 *
 * The form is split into two steps on purpose: a provider (endpoint + key) is
 * shared, a model (id + limits) is added to it. That mirrors how these services
 * actually work and removes the old "preset: auto" trap, where a wrong base
 * URL silently produced a preset that could never run.
 *
 * "Sniff" asks the endpoint for its model list. It is strictly a convenience:
 * a gateway that does not implement `/models` is not broken, so a failed probe
 * shows the reason and the form stays typeable.
 */

import { useCallback, useEffect, useState } from "react";
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  CopyIcon,
  Loader2Icon,
  PlusIcon,
  RadarIcon,
  SaveIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { InfoHint } from "@/components/ui/info-hint";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  api,
  type ModelEntry,
  type ModelOption,
  type Provider,
} from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

const EMPTY_MODEL: ModelEntry = {
  id: "",
  label: "",
  max_context: null,
  max_output: 28000,
  multimodal: false,
  temperature: 0.7,
  top_p: 0.8,
  thinking: false,
  thinking_effort: null,
  price_in: null,
  price_out: null,
  currency: "USD",
};

interface Draft {
  id: string | null;
  name: string;
  base_url: string;
  protocol: string;
  max_context: number | null;
  max_output: number;
  enabled: boolean;
  api_key: string;
}

const EMPTY_DRAFT: Draft = {
  id: null,
  name: "",
  base_url: "",
  protocol: "openai",
  max_context: null,
  max_output: 28000,
  enabled: true,
  api_key: "",
};

function providerToDraft(provider: Provider): Draft {
  return {
    id: provider.id,
    name: provider.name,
    base_url: provider.base_url,
    protocol: provider.protocol,
    max_context: provider.max_context,
    max_output: provider.max_output,
    enabled: provider.enabled,
    api_key: "",
  };
}

export function ProviderPanel({
  providers,
  models,
  active,
  onReload,
}: {
  providers: Provider[];
  models: ModelOption[];
  active: string | null;
  onReload: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [modelDraft, setModelDraft] = useState<ModelEntry>(EMPTY_MODEL);
  const [probed, setProbed] = useState<string[]>([]);
  const [probing, setProbing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(providers[0]?.id ?? null);
  //: The delete button only opens the dialog; nothing is destroyed on one click.
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (selected === null && providers.length > 0) setSelected(providers[0].id);
  }, [providers, selected]);

  const provider = providers.find((item) => item.id === selected) ?? null;

  const reload = useCallback(() => {
    onReload();
  }, [onReload]);

  const probe = async () => {
    setProbing(true);
    setProbeError(null);
    try {
      const result = await api.probeProvider(
        draft.base_url,
        draft.api_key || "",
        draft.protocol,
      );
      if (!result.ok) {
        setProbed([]);
        setProbeError(result.error ?? "嗅探失败");
        return;
      }
      setProbed(result.models);
      if (result.models.length === 0) {
        setProbeError("接口返回了空列表；可以手动填写模型 ID。");
      } else {
        toast.success(
          `发现 ${result.models.length} 个模型${result.latency_ms ? `（${result.latency_ms} ms）` : ""}`,
        );
      }
    } catch (error) {
      setProbeError(String(error).replace(/^Error:\s*/, ""));
    } finally {
      setProbing(false);
    }
  };

  const saveProvider = async () => {
    if (!draft.name.trim()) {
      toast.error("先填一个名称");
      return;
    }
    if (!/^https?:\/\//.test(draft.base_url.trim())) {
      toast.error("Base URL 必须以 http:// 或 https:// 开头");
      return;
    }
    setSaving(true);
    try {
      const saved = await api.saveProvider({
        id: draft.id,
        name: draft.name.trim(),
        base_url: draft.base_url.trim(),
        protocol: draft.protocol,
        max_context: draft.max_context,
        max_output: draft.max_output,
        enabled: draft.enabled,
        api_key: draft.api_key ? draft.api_key : undefined,
        models: provider?.models ?? [],
      });
      setDraft(providerToDraft(saved));
      setSelected(saved.id);
      reload();
      toast.success("Provider 已保存，密钥已加密存储");
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  };

  const addModel = async (modelId?: string) => {
    const id = (modelId ?? modelDraft.id).trim();
    if (!selected) {
      toast.error("先保存 Provider");
      return;
    }
    if (!id) {
      toast.error("模型 ID 不能为空");
      return;
    }
    try {
      await api.addProviderModel(selected, { ...modelDraft, id });
      setModelDraft(EMPTY_MODEL);
      reload();
      toast.success(`已添加 ${id}`);
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    }
  };

  const removeModel = async (modelId: string) => {
    if (!selected) return;
    try {
      await api.removeProviderModel(selected, modelId);
      reload();
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    }
  };

  const removeProvider = async () => {
    if (!selected) return;
    try {
      await api.deleteProvider(selected);
      setSelected(null);
      setDraft(EMPTY_DRAFT);
      reload();
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    }
  };

  const activate = async (key: string) => {
    try {
      await api.activateModel(key);
      reload();
      toast.success("已切换模型");
    } catch (error) {
      toast.error(String(error).replace(/^Error:\s*/, ""));
    }
  };

  return (
    <div className="flex min-h-[24rem] gap-3">
      <div className="flex w-48 shrink-0 flex-col gap-1">
        {providers.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              setSelected(item.id);
              setDraft(providerToDraft(item));
              setProbed([]);
              setProbeError(null);
            }}
            className={cn(
              "flex flex-col gap-0.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
              selected === item.id && "bg-accent",
            )}
          >
            <span className="flex items-center gap-1.5 truncate">
              {item.enabled ? (
                <CheckCircle2Icon className="size-3 rotate-0 text-emerald-500" />
              ) : (
                <CircleAlertIcon className="size-3 text-muted-foreground" />
              )}
              {item.name}
            </span>
            <span className="truncate text-[11px] text-muted-foreground">
              {item.models.length} 个模型
              {item.has_api_key ? "" : " · 无密钥"}
            </span>          </button>
        ))}
        <Button
          variant="outline"
          size="sm"
          className="mt-1"
          onClick={() => {
            setSelected(null);
            setDraft(EMPTY_DRAFT);
            setProbed([]);
            setProbeError(null);
          }}
        >
          <PlusIcon />
          新建 Provider
        </Button>
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <div className="grid grid-cols-2 gap-2">
          <div className="col-span-2 flex flex-col gap-1">
            <Label htmlFor="pv-name" className="text-xs">
              名称
            </Label>
            <Input
              id="pv-name"
              value={draft.name}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, name: event.target.value }))
              }
              placeholder="自定义名称"
              className="h-8 text-xs"
            />
          </div>
          <div className="col-span-2 flex flex-col gap-1">
            <Label htmlFor="pv-url" className="text-xs">
              Base URL
            </Label>
            <Input
              id="pv-url"
              value={draft.base_url}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, base_url: event.target.value }))
              }
              placeholder="https://api.example.com/v1"
              className="h-8 font-mono text-xs"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="pv-key" className="text-xs">
              API Key
            </Label>
            <Input
              id="pv-key"
              type="password"
              value={draft.api_key}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, api_key: event.target.value }))
              }
              placeholder={provider?.has_api_key ? "已保存，留空则不修改" : "sk-..."}
              className="h-8 text-xs"
            />
          </div>
          <div className="flex flex-col gap-1">
            <Label htmlFor="pv-proto" className="text-xs">
              协议
            </Label>
            <select
              id="pv-proto"
              value={draft.protocol}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, protocol: event.target.value }))
              }
              className="h-8 rounded-md border border-border bg-background px-2 text-xs outline-none"
            >
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={saveProvider} disabled={saving}>
            {saving ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <SaveIcon className="size-3.5" />
            )}
            保存 Provider
          </Button>
          <Button size="sm" variant="outline" onClick={probe} disabled={probing}>
            {probing ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <RadarIcon className="size-3.5" />
            )}
            嗅探模型
          </Button>
          {provider && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2Icon className="size-3.5" />
              删除
            </Button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Label htmlFor="pv-enabled" className="text-xs text-muted-foreground">
              启用
            </Label>
            <Switch
              id="pv-enabled"
              checked={draft.enabled}
              title="停用后这个 Provider 的模型不会出现在模型下拉里"
              onCheckedChange={(value) =>
                setDraft((prev) => ({ ...prev, enabled: value }))
              }
            />
          </div>
        </div>

        {probeError && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
            <CircleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
            <span className="min-w-0 break-all">{probeError}</span>
          </div>
        )}

        {provider && !provider.has_api_key && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/5 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            这个 Provider 没有保存 API Key。本地推理服务、内网网关、带额外认证头的部署
            常常不需要密钥，所以这里只是提醒、不会阻止发送；请求会以无认证头的方式发出。
          </p>
        )}

        {probed.length > 0 && (
          <div className="flex flex-col gap-1 rounded-md border border-border p-2">
            <span className="text-[11px] text-muted-foreground">
              端点报告的模型（点击加入）：
            </span>
            <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto">
              {probed.map((name) => {
                const exists = provider?.models.some((item) => item.id === name);
                return (
                  <button
                    key={name}
                    type="button"
                    disabled={exists || !selected}
                    onClick={() => addModel(name)}
                    className={cn(
                      "rounded border border-border px-1.5 py-0.5 font-mono text-[11px] transition-colors",
                      exists
                        ? "cursor-default opacity-40"
                        : "hover:bg-accent",
                      !selected && "cursor-not-allowed opacity-40",
                    )}
                  >
                    {name}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <span className="text-xs font-medium">模型</span>
          {(provider?.models ?? []).map((entry) => {
            const key = `${provider?.id}::${entry.id}`;
            const isActive = key === active;
            return (
              <div
                key={entry.id}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5"
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <code className="truncate text-xs">{entry.id}</code>
                    {entry.multimodal && (
                      <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">
                        图像
                      </span>
                    )}
                    {entry.thinking && (
                      <span className="rounded bg-amber-500/10 px-1 text-[10px] text-amber-700 dark:text-amber-400">
                        原生思考
                      </span>
                    )}
                    {isActive && (
                      <span className="rounded bg-primary/10 px-1 text-[10px] text-primary">
                        使用中
                      </span>
                    )}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    ctx {entry.max_context ?? "默认"} · out {entry.max_output}
                    {entry.price_in !== null && ` · ${entry.price_in}/${entry.price_out} ${entry.currency}`}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={isActive}
                  onClick={() => activate(key)}
                >
                  选用
                </Button>
                <Button
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => removeModel(entry.id)}
                  aria-label="删除模型"
                >
                  <Trash2Icon className="size-3.5" />
                </Button>
              </div>
            );
          })}
          {provider && provider.models.length === 0 && (
            <p className="text-[11px] text-muted-foreground">
              还没有模型。先保存 Provider，再用嗅探或下面的表单添加一个。
            </p>
          )}

          <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <span>原生思考按模型开关</span>
            <InfoHint label="原生思考说明">
              模型自己产出推理过程。开启后请求会带上 <code>thinking</code> 参数，
              服务商真实产生推理 token，按输出计费；同时会把强制的{" "}
              <code>tool_choice</code> 降级为 <code>auto</code>。
              <span className="text-amber-700 dark:text-amber-400">
                模型不支持时可能直接报错
              </span>
              ，所以不确定就关掉。它和「核心配置」里的 reasoning 选项不是一回事：
              后者只改提示词，任何模型都能用，两者同时开启会重复消耗输出预算。
            </InfoHint>
          </div>

          <div className="grid grid-cols-4 gap-2 rounded-md border border-dashed border-border p-2">
            <div className="col-span-2 flex flex-col gap-1">
              <Label htmlFor="m-id" className="text-[11px]">
                模型 ID
              </Label>
              <Input
                id="m-id"
                value={modelDraft.id}
                onChange={(event) =>
                  setModelDraft((prev) => ({ ...prev, id: event.target.value }))
                }
                placeholder="填入服务商提供的模型 ID"
                className="h-7 font-mono text-xs"
              />
            </div>
            <div className="col-span-2 flex flex-col gap-1">
              <Label htmlFor="m-ctx" className="text-[11px]">
                上下文上限
              </Label>
              <Input
                id="m-ctx"
                type="number"
                value={modelDraft.max_context ?? ""}
                onChange={(event) =>
                  setModelDraft((prev) => ({
                    ...prev,
                    max_context: event.target.value
                      ? Number(event.target.value)
                      : null,
                  }))
                }
                placeholder="留空用默认"
                className="h-7 text-xs"
              />
            </div>
            <div className="col-span-2 flex flex-col gap-1">
              <Label htmlFor="m-out" className="text-[11px]">
                最大输出
              </Label>
              <Input
                id="m-out"
                type="number"
                value={modelDraft.max_output}
                onChange={(event) =>
                  setModelDraft((prev) => ({
                    ...prev,
                    max_output: Number(event.target.value),
                  }))
                }
                className="h-7 text-xs"
              />
            </div>
            <div className="col-span-4 flex items-end justify-between gap-2">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <Switch
                    id="m-mm"
                    checked={modelDraft.multimodal}
                    title="这个模型能接受图片输入；关闭后图片附件会被拒绝"
                    onCheckedChange={(value) =>
                      setModelDraft((prev) => ({ ...prev, multimodal: value }))
                    }
                  />
                  <Label htmlFor="m-mm" className="text-[11px]">
                    支持图像
                  </Label>
                </div>
                <div className="flex items-center gap-1.5">
                  <Switch
                    id="m-thinking"
                    checked={modelDraft.thinking}
                    title="向 Provider 请求它自己的思考通道；不支持该字段的模型可能直接报错"
                    onCheckedChange={(value) =>
                      setModelDraft((prev) => ({ ...prev, thinking: value }))
                    }
                  />
                  <Label htmlFor="m-thinking" className="text-[11px]">
                    原生思考
                  </Label>
                </div>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => addModel()}
                disabled={!selected}
              >
                <PlusIcon />
                添加
              </Button>
            </div>
          </div>
        </div>

        {models.length > 0 && (
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>已配置 {models.length} 个可切换模型；</span>
            <CopyKeyButton value={active ?? ""} />
            <code className="break-all">{active ?? "未选择"}</code>
          </p>
        )}
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="删除这个 Provider？"
        description={
          <>
            它的所有模型和已保存的密钥会一起删掉，无法恢复。
            <br />
            只是暂时不想用的话，把上面的「启用」关掉即可。
          </>
        }
        confirmLabel="删除"
        destructive
        onConfirm={() => void removeProvider()}
      />
    </div>
  );
}

/**
 * Copies the active model key. The clipboard API needs a secure context, which
 * `http://127.0.0.1` satisfies but a LAN address (`http://192.168.x.x:8000`)
 * does not, so a plain-textarea fallback keeps the button usable there.
 */
export function CopyKeyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (!value) {
      toast.error("还没有选中的模型");
      return;
    }
    if (await copyText(value)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
      toast.success("已复制模型标识", { id: "copy-model-key" });
    } else {
      toast.error("复制失败，请手动选中后复制", { id: "copy-model-key" });
    }
  };

  return (
    <button
      type="button"
      onClick={() => {
        void copy();
      }}
      title="复制模型标识"
      aria-label="复制模型标识"
      className={cn(
        "inline-flex items-center gap-0.5 rounded px-1 py-0.5 transition-colors",
        "hover:bg-accent hover:text-foreground",
        copied && "text-emerald-600 dark:text-emerald-400",
      )}
    >
      {copied ? (
        <CheckCircle2Icon className="size-3" />
      ) : (
        <CopyIcon className="size-3" />
      )}
      <span className="text-[11px]">{copied ? "已复制" : "复制"}</span>
    </button>
  );
}
