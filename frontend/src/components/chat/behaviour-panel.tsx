/**
 * Behaviour tab: what a run does when it is about to stop on its own.
 *
 * The two policies are genuinely different, and the UI has to say so:
 *
 * - **优雅停机** ends the turn. The only number involved is how many *refusal*
 *   rounds the model gets afterwards — rounds where its tool requests are
 *   answered with a refusal so it can close with what it already knows, instead
 *   of being cut off mid-sentence.
 * - **挂起并询问** also ends the turn, but first asks the user. Answering
 *   "继续迭代" starts a **new turn** with a *normal* budget; it does not extend
 *   the current one. A budget that can be raised on demand is not a budget, and
 *   the conversation state belongs to a turn, not to an ever-growing loop.
 *
 * That is why there is no "追加轮数" control any more: there is nothing to add.
 */

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { InfoHint } from "@/components/ui/info-hint";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type AppSettings } from "@/lib/api";
import { cn } from "@/lib/utils";

/** ``String(x ?? d)`` would turn a missing field into the literal "undefined". */
function text(value: number | null | undefined, fallback: number): string {
  return value === null || value === undefined ? String(fallback) : String(value);
}

export function BehaviourPanel({
  settings,
  onSaved,
}: {
  settings: AppSettings;
  onSaved: (settings: AppSettings) => void;
}) {
  const [refusalRounds, setRefusalRounds] = useState(
    text(settings.tool_limit_extension, 5),
  );
  const [timeout, setTimeoutValue] = useState(text(settings.tool_limit_timeout, 300));
  const [saving, setSaving] = useState(false);

  const save = async (patch: Partial<AppSettings>) => {
    setSaving(true);
    try {
      const saved = await api.patchSettings(patch);
      onSaved(saved);
      setRefusalRounds(text(saved.tool_limit_extension, 5));
      setTimeoutValue(text(saved.tool_limit_timeout, 300));
      toast.success("已保存");
    } catch (error) {
      toast.error(`保存失败：${String(error).replace(/^Error:\s*/, "")}`);
    } finally {
      setSaving(false);
    }
  };

  const ask = settings.tool_limit_policy === "ask";

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">工具预算用尽时</h3>
          <InfoHint>
            一轮 = 模型请求一次工具。预算本身由 AmritaCore 的{" "}
            <code>agent_tool_call_limit</code> 控制（核心配置 → 函数），
            这里只决定「用完之后怎么办」。
          </InfoHint>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              {
                value: "graceful",
                title: "优雅停机",
                detail:
                  "本轮结束，模型用已有信息收尾回答。不打断、不等待，适合无人值守；收尾能跑几轮在下面设置。",
              },
              {
                value: "ask",
                title: "挂起并询问",
                detail:
                  "先停住，问你要怎么办：继续迭代（开启新一轮，预算重新计）、优雅收尾（不再调工具，用它已有的信息写完）或直接中断。不选或超时按优雅收尾处理。",
              },
            ] as const
          ).map((option) => (
            <button
              key={option.value}
              type="button"
              disabled={saving}
              onClick={() => {
                if (settings.tool_limit_policy !== option.value) {
                  void save({ tool_limit_policy: option.value });
                }
              }}
              className={cn(
                "flex flex-col gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors",
                settings.tool_limit_policy === option.value
                  ? "border-primary bg-primary/5"
                  : "border-border hover:bg-muted/40",
              )}
            >
              <span className="text-sm font-medium">{option.title}</span>
              <span className="text-xs leading-5 text-muted-foreground">
                {option.detail}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">收尾轮数</h3>
          <InfoHint label="收尾轮数说明">
            预算用尽后，模型可能还会请求几次工具。这几轮里每个请求都会被拒绝、
            并把原因返回给模型，而不是真正执行 —— 目的是让它把已有结论说完，
            而不是被硬生生截断。两种模式下都生效：选「继续迭代」时，本轮同样
            先按这个预算收尾，然后才开启新一轮。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="limit-refusal">收尾轮数</Label>
            <Input
              id="limit-refusal"
              type="number"
              min={1}
              max={50}
              value={refusalRounds}
              disabled={saving}
              onChange={(event) => setRefusalRounds(event.target.value)}
              className="w-28"
            />
          </div>
          {ask && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="limit-timeout">等待上限（秒）</Label>
              <Input
                id="limit-timeout"
                type="number"
                min={10}
                max={3600}
                value={timeout}
                disabled={saving}
                onChange={(event) => setTimeoutValue(event.target.value)}
                className="w-28"
              />
            </div>
          )}
          <Button
            disabled={saving}
            onClick={() =>
              void save(
                ask
                  ? {
                      tool_limit_extension: Number(refusalRounds),
                      tool_limit_timeout: Number(timeout),
                    }
                  : { tool_limit_extension: Number(refusalRounds) },
              )
            }
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          对应 AmritaCore 的 <code>agent_tool_refusal_rounds</code>；
          这里设置的值会在每次运行时覆盖核心配置。
          {ask && " 等待上限只用于「挂起并询问」。"}
        </p>
      </section>

      <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs leading-5 text-muted-foreground">
        「继续迭代」不会延长本轮的预算：本轮按上面的收尾轮数结束，然后自动开启
        新一轮，预算按 <code>agent_tool_call_limit</code> 重新计算。
        能继续多少次由你决定——服务端不会替你划一个上限。
      </p>
    </div>
  );
}
