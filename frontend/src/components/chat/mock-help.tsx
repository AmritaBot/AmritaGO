/**
 * What the front-end test mode can be made to show.
 *
 * Test mode answers every prompt locally, so the only way to see, say, the
 * approval dialog or the budget gate is to know which words ask for them. That
 * list lived only in the source, which is the same as not existing; it belongs
 * behind a "?" next to the switch.
 */

import { InfoHint } from "@/components/ui/info-hint";

/**
 * One line per demo. The keywords are matched against the whole prompt, so a
 * shorter word is enough; they are shown as the user would type them.
 */
const TRIGGERS: { label: string; keywords: string[] }[] = [
  { label: "待办面板", keywords: ["计划", "待办", "todo", "plan"] },
  {
    label: "审批面板（可驳回）",
    keywords: ["审批", "确认", "audit", "confirm"],
  },
  {
    label: "两个审批同时排队（各自答复）",
    keywords: ["并行审批", "多个审批", "同时审批", "parallel"],
  },
  {
    label: "预算询问（继续 / 收尾 / 中断）",
    keywords: ["预算", "继续迭代", "budget"],
  },
  { label: "手动取消工具（填原因）", keywords: ["取消", "中止", "cancel"] },
  { label: "工具超时", keywords: ["超时", "timeout"] },
  { label: "上下文压缩提示", keywords: ["压缩", "compact", "摘要"] },
  { label: "代码块与表格", keywords: ["代码", "code", "表格", "table"] },
  { label: "图表（mermaid）", keywords: ["图", "mermaid", "flow"] },
  { label: "HTML 预览", keywords: ["html", "网页"] },
];

export function MockTriggersNote() {
  return (
    <div className="flex max-h-72 flex-col gap-2 overflow-y-auto pr-1">
      <p className="text-muted-foreground">
        这是个不发请求的假模式：回复由浏览器拼出来，用来检查渲染。提问里带上下面这些词，就会出现对应的演示。
      </p>
      <dl className="flex flex-col gap-1.5">
        {TRIGGERS.map((item) => (
          <div key={item.label} className="flex flex-col gap-0.5">
            <dt className="text-foreground">{item.label}</dt>
            <dd className="flex flex-wrap gap-1">
              {item.keywords.map((keyword) => (
                <code
                  key={keyword}
                  className="rounded border border-border bg-muted px-1 py-0.5 font-mono text-[10px] text-foreground"
                >
                  {keyword}
                </code>
              ))}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-muted-foreground">
        其它任何内容都会得到一条普通回复：流式文本 + 一段思考 + 一次工具调用 + 用量。
        一轮只看第一次提问，且全部留在内存里，刷新即消失，不会写进会话。
      </p>
    </div>
  );
}

/** The "?" that sits next to the test-mode switch and the footer badge. */
export function MockHint({ side = "bottom" }: { side?: "bottom" | "top" }) {
  return (
    <InfoHint label="前端测试模式能触发什么" side={side}>
      <MockTriggersNote />
    </InfoHint>
  );
}
