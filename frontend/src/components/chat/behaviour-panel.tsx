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

/** ``Number("")`` is 0, which would silently mean "no limit"; refuse it instead. */
function parseAll(values: string[]): number[] | null {
  const parsed = values.map((value) =>
    value.trim() === "" ? Number.NaN : Number(value),
  );
  return parsed.every(Number.isFinite) ? parsed : null;
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
  const [contextLimit, setContextLimit] = useState(
    text(settings.context_message_limit, 200),
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
      setContextLimit(text(saved.context_message_limit, 200));
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
            一问一答算<strong>一轮</strong>
            ，模型每发一次工具请求算
            <strong>一次 LLM 请求</strong>。预算由核心配置的{" "}
            <code>agent_tool_call_limit</code> 决定，这里只决定用完之后怎么办。
          </InfoHint>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {(
            [
              {
                value: "graceful",
                title: "优雅停机",
                detail: "本轮结束，模型用已有信息收尾。不打断、不等待。",
              },
              {
                value: "ask",
                title: "挂起并询问",
                detail:
                  "先停住，让你选继续迭代、优雅收尾或直接中断；不选按优雅收尾处理。",
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
            <strong>追加请求数</strong>
            ：每次「继续迭代」往本轮加的 LLM 请求数，上下文不丢。
            <strong>收尾请求数</strong>
            ：收尾时还能发几次工具请求，每次都会被拒绝，让模型把结论说完。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="limit-extension">追加请求数</Label>
            <Input
              id="limit-extension"
              type="number"
              min={1}
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
                min={1}
                value={timeout}
                disabled={saving}
                onChange={(event) => setTimeoutValue(event.target.value)}
                className="w-28"
              />
            </div>
          )}
          <Button
            disabled={saving}
            onClick={() => {
              const parsed = parseAll(
                ask
                  ? [extension, refusalRequests, timeout]
                  : [extension, refusalRequests],
              );
              if (!parsed) {
                toast.error("请填写有效的数字");
                return;
              }
              const [ext, refusals, wait] = parsed;
              void save(
                ask
                  ? {
                      tool_limit_extension: ext,
                      tool_limit_refusal_rounds: refusals,
                      tool_limit_timeout: wait,
                    }
                  : {
                      tool_limit_extension: ext,
                      tool_limit_refusal_rounds: refusals,
                    },
              );
            }}
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          对应 <code>agent_tool_refusal_rounds</code>，运行时覆盖核心配置。
          {ask && " 等待上限仅用于「挂起并询问」。"}
        </p>
      </section>

      <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs leading-5 text-muted-foreground">
        「继续迭代」在本轮内追加请求，模型带着现有上下文接着做，不新开会话、不重放历史。
      </p>

      <section className="flex flex-col gap-3 rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">工具执行</h3>
          <InfoHint label="工具超时说明">
            单次工具调用的<strong>墙钟上限</strong>。超过就终止这次调用，
            把「已超时」作为结果交给模型，本轮继续——不是杀掉整个回答。 填{" "}
            <code>-1</code> 表示<strong>永不超时</strong>
            。工具自己的上限会先到；
            等待人工确认的时间也计入，所以请保持它大于确认等待上限。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="tool-timeout">全局超时（秒）</Label>
            <Input
              id="tool-timeout"
              type="number"
              min={-1}
              value={toolTimeout}
              disabled={saving}
              onChange={(event) => setToolTimeout(event.target.value)}
              className="w-32"
            />
          </div>
          <Button
            disabled={saving}
            onClick={() => {
              const parsed = parseAll([toolTimeout]);
              if (!parsed) {
                toast.error("请填写有效的数字");
                return;
              }
              void save({ tool_timeout: parsed[0] });
            }}
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          对话里正在运行的调用可以随时「取消」并填写原因，原因会作为结果交给模型。
        </p>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border border-border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">上下文</h3>
          <InfoHint label="上下文消息条数说明">
            进入模型上下文的<strong>消息条数</strong>上限（不是 token）。达到后{" "}
            <code>compact</code> 折叠成摘要，<code>slide</code> 直接丢弃最早的。
            工具调用与结果也算条数。填 <code>-1</code> 表示不按条数限制，只靠
            token 阈值。
          </InfoHint>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="context-limit">消息条数上限</Label>
            <Input
              id="context-limit"
              type="number"
              min={-1}
              value={contextLimit}
              disabled={saving}
              onChange={(event) => setContextLimit(event.target.value)}
              className="w-32"
            />
          </div>
          <Button
            disabled={saving}
            onClick={() => {
              const parsed = parseAll([contextLimit]);
              if (!parsed) {
                toast.error("请填写有效的数字");
                return;
              }
              void save({ context_message_limit: parsed[0] });
            }}
          >
            保存
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          对应 AmritaCore 的 <code>llm.memory_length_limit</code>
          ，这里设置的值会在每次运行时覆盖核心配置。
          是否真的裁剪还取决于「核心配置 → 模型请求 → 上下文超限策略」：选{" "}
          <code>none</code> 时历史不受任何限制，只由服务商报错。
        </p>
      </section>
    </div>
  );
}
