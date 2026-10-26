/**
 * Behaviour tab: what a run does when it is about to stop on its own.
 *
 * One user message is a **轮**; every tool-calling turn the model takes inside
 * it is an **LLM 请求**. The budget counts requests, and the two policies are
 * genuinely different, so the UI has to say so:
 *
 * - **优雅停机** ends the turn. The only number involved is how many *refusal*
 *   requests the model gets afterwards — requests where its tool calls are
 *   answered with a refusal so it can close with what it already knows, instead
 *   of being cut off mid-sentence.
 * - **挂起并询问** also stops at the budget, but first asks the user. Answering
 *   "继续迭代" appends `tool_limit_extension` requests to **this** turn, so the
 *   agent keeps going with its context intact; the wind-down number still
 *   applies if the user picks 优雅收尾 instead.
 *
 * That is why there are two numbers, not one: one is how much more work a grant
 * buys, the other is how much room a wind-down gets.
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
  return value === null || value === undefined
    ? String(fallback)
    : String(value);
}

export function BehaviourPanel({
  settings,
  onSaved,
}: {
  settings: AppSettings;
  onSaved: (settings: AppSettings) => void;
}) {
  const [extension, setExtension] = useState(
    text(settings.tool_limit_extension, 5),
  );
  const [refusalRequests, setRefusalRequests] = useState(
    text(settings.tool_limit_refusal_rounds, 5),
  );
  const [timeout, setTimeoutValue] = useState(
    text(settings.tool_limit_timeout, 300),
  );
  const [toolTimeout, setToolTimeout] = useState(
    text(settings.tool_timeout, 600),
  );
  const [saving, setSaving] = useState(false);

  const save = async (patch: Partial<AppSettings>) => {
    setSaving(true);
    try {
      const saved = await api.patchSettings(patch);
      onSaved(saved);
      setExtension(text(saved.tool_limit_extension, 5));
      setRefusalRequests(text(saved.tool_limit_refusal_rounds, 5));
      setTimeoutValue(text(saved.tool_limit_timeout, 300));
      setToolTimeout(text(saved.tool_timeout, 600));
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
            你和 Agent 的一问一答算<strong>一轮</strong>
            ；一轮里模型每发一次工具请求算
            <strong>一次 LLM 请求</strong>。预算由 AmritaCore 的{" "}
            <code>agent_tool_call_limit</code> 控制（核心配置 → 函数），按 LLM
            请求次数计算， 这里只决定「用完之后怎么办」。
          </InfoHint>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              {
                value: "graceful",
                title: "优雅停机",
                detail:
                  "本轮结束，模型用已有信息收尾回答。不打断、不等待，适合无人值守；收尾还能发几次请求在下面设置。",
              },
              {
                value: "ask",
                title: "挂起并询问",
                detail:
                  "先停住，问你要怎么办：继续迭代（在本轮内追加 LLM 请求次数，上下文不丢）、优雅收尾（不再调工具，用它已有的信息写完）或直接中断。不选或超时按优雅收尾处理。",
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
          <h3 className="text-sm font-medium">请求数</h3>
          <InfoHint label="请求数说明">
            两个数字管的是不同的东西：<strong>追加请求数</strong>
            是每次回答「继续迭代」 时往<strong>本轮</strong>里加的 LLM
            请求次数，加完模型接着做，上下文一条不丢；
            <strong>收尾请求数</strong>是模型收尾时还能发几次工具请求 ——
            这几次里每个请求
            都会被拒绝并把原因返回给模型，让它把已有结论说完，而不是被硬生生截断。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="limit-extension">追加请求数</Label>
            <Input
              id="limit-extension"
              type="number"
              min={1}
              max={100}
              value={extension}
              disabled={saving}
              onChange={(event) => setExtension(event.target.value)}
              className="w-28"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="limit-refusal">收尾请求数</Label>
            <Input
              id="limit-refusal"
              type="number"
              min={1}
              max={50}
              value={refusalRequests}
              disabled={saving}
              onChange={(event) => setRefusalRequests(event.target.value)}
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
                      tool_limit_extension: Number(extension),
                      tool_limit_refusal_rounds: Number(refusalRequests),
                      tool_limit_timeout: Number(timeout),
                    }
                  : {
                      tool_limit_extension: Number(extension),
                      tool_limit_refusal_rounds: Number(refusalRequests),
                    },
              )
            }
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          收尾请求数对应 AmritaCore 的 <code>agent_tool_refusal_rounds</code>，
          这里设置的值会在每次运行时覆盖核心配置。
          {ask && " 等待上限只用于「挂起并询问」。"}
        </p>
      </section>

      <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs leading-5 text-muted-foreground">
        「继续迭代」在本轮内追加 LLM 请求：预算上限变成「已用请求数 +
        追加请求数」， 模型直接从当前上下文继续，不新开会话、不重放历史。
        能继续多少次由你决定——服务端不会替你划一个上限。
      </p>

      <section className="flex flex-col gap-3 rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">工具执行</h3>
          <InfoHint label="工具超时说明">
            单次工具调用的<strong>墙钟上限</strong>。超过就终止这次调用，
            把「已超时」作为结果交给模型，本轮继续——不是杀掉整个回答。 填{" "}
            <code>0</code> 表示不限制。工具自己设的更短上限（例如 shell 的{" "}
            <code>timeout</code>{" "}
            参数）会先到，因此仍然生效；等待人工确认的时间也算在这里，
            所以请保持它大于确认等待上限。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tool-timeout">全局超时（秒）</Label>
            <Input
              id="tool-timeout"
              type="number"
              min={0}
              max={86400}
              value={toolTimeout}
              disabled={saving}
              onChange={(event) => setToolTimeout(event.target.value)}
              className="w-32"
            />
          </div>
          <Button
            disabled={saving}
            onClick={() => void save({ tool_timeout: Number(toolTimeout) })}
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          运行中的工具调用可以在对话里的那一行直接「取消」，并填写原因；
          原因会作为工具结果交给模型，它可以用别的方式继续。
        </p>
      </section>
    </div>
  );
}
