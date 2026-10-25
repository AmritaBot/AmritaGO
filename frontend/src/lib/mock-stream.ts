/**
 * Front-end-only test stream.
 *
 * The point is to exercise the *rendering* path — streaming, reasoning,
 * tool-event pills, usage footer, markdown, collapse, edge cases — without a
 * provider, a key, or a round trip. Nothing here is sent anywhere; the reply is
 * generated in the browser and disappears on reload.
 *
 * Enabled from 设置 -> 插件 -> 开发者选项. Kept out of the normal path on purpose:
 * a fake answer that looks real is a trap if it is one switch away from the
 * default.
 */

import type { StreamEvent } from "@/lib/api";

const STORAGE_KEY = "amrita:mock-stream";

/**
 * The workflow title a fake turn reports.
 *
 * Exported because the footer badge has to tell a fabricated run from a real
 * one, and the title is the only part of the `start` event that says so.
 */
export const MOCK_WORKFLOW_TITLE = "前端测试模式";

export function isMockMode(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setMockMode(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(STORAGE_KEY, "1");
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // A blocked localStorage is not a reason to break the panel.
  }
  window.dispatchEvent(new CustomEvent("amrita:mock-mode", { detail: value }));
}

const REASONING = [
  "先看用户到底要什么：这是前端测试模式，没有真的模型。",
  "所以回复只需要覆盖几种渲染路径，尤其是代码块、表格和容器宽度。",
  "给一个短回答就够了，别把界面撑得太长。",
];

/** Thinking that happens *after* a tool ran, so the timeline is not flat. */
const POST_TOOL_REASONING = [
  "工具回来了：三个文件、没有 TODO，说明结论可以短一点。",
  "那就直接给结论，不用再调工具了。",
];

/** Prefix of a fabricated confirmation, so the answer can be routed locally. */
const MOCK_CONFIRM_PREFIX = "mock-confirm-";

/**
 * Does the prompt ask for the audit demo?
 *
 * The confirmation dialog blocks a real turn server-side, so without this the
 * component could only be seen by triggering a real destructive command. In
 * mock mode the same dialog is driven by a fabricated request and answered
 * in-browser, which is exactly what test mode is for.
 */
export function wantsAuditDemo(prompt: string): boolean {
  return /审批|确认|audit|confirm/i.test(prompt);
}

export function isMockConfirm(requestId: string): boolean {
  return requestId.startsWith(MOCK_CONFIRM_PREFIX);
}

/** Does the prompt ask for the plan-panel demo? */
export function wantsTodoDemo(prompt: string): boolean {
  return /计划|待办|todo|plan/i.test(prompt);
}

const MOCK_TODOS = {
  title: "重构解析器",
  total: 4,
  done: 1,
  items: [
    { id: "1", text: "读一遍现有解析器与测试", done: true },
    { id: "2", text: "拆出 tokenizer，保持现有行为", done: false },
    { id: "3", text: "补齐错误位置的用例", done: false },
    { id: "4", text: "跑一遍全量测试并更新文档", done: false },
  ],
};

/** Prefix of a fabricated budget gate, so the answer can be routed locally. */
const MOCK_LIMIT_PREFIX = "mock-limit-";

/** Does the prompt ask for the "continue iterating?" demo? */
export function wantsLimitDemo(prompt: string): boolean {
  return /预算|继续迭代|budget|continue|iterate/i.test(prompt);
}

/** Does the prompt ask for the context-compaction demo? */
export function wantsCompactDemo(prompt: string): boolean {
  return /压缩|compact|摘要/i.test(prompt);
}

export function isMockLimit(requestId: string): boolean {
  return requestId.startsWith(MOCK_LIMIT_PREFIX);
}

function answerFor(prompt: string): string {
  const text = prompt.toLowerCase();
  if (text.includes("html") || text.includes("网页")) {
    return [
      "下面是一段可以预览的 HTML：",
      "",
      "```html",
      "<!doctype html>",
      '<html lang="zh-CN">',
      "  <head>",
      '    <meta charset="utf-8" />',
      "    <title>404 Not Found</title>",
      "    <style>",
      "      body { font-family: system-ui, sans-serif; display: grid; place-items: center; height: 100vh; margin: 0; background: #faf7f2; }",
      "      h1 { font-size: 3rem; margin: 0 0 .5rem; color: #7a4b12; }",
      "      p { color: #6b7280; }",
      "    </style>",
      "  </head>",
      "  <body>",
      "    <div>",
      "      <h1>404 Not Found</h1>",
      "      <p>这个页面不存在。</p>",
      "    </div>",
      "  </body>",
      "</html>",
      "```",
      "",
      "点右上角的「运行」可以在弹窗里看效果。",
    ].join("\n");
  }
  if (text.includes("代码") || text.includes("code") || text.includes("```")) {
    return [
      "## 代码块测试",
      "",
      "下面这段用来确认横向滚动不会把气泡撑破：",
      "",
      "```python",
      "def greet(name: str) -> str:",
      '    """Return a greeting that is long enough to matter."""',
      '    return f"hello, {name}! this line is intentionally long so the block scrolls instead of wrapping"',
      "```",
      "",
      "- 行内 `code` 应该有底色",
      "- 列表缩进应该对齐",
    ].join("\n");
  }
  if (text.includes("表") || text.includes("table")) {
    return [
      "| 能力 | 状态 | 说明 |",
      "| --- | --- | --- |",
      "| 流式 | 是 | 这段文字是被切成小块推过来的 |",
      "| 思考 | 是 | 上面的「思考过程」可展开 |",
      "| 工具事件 | 是 | 下面有一条假的调用记录 |",
      "",
      "> 引用块的左边线应该跟着主题色走。",
    ].join("\n");
  }
  if (text.includes("图") || text.includes("mermaid") || text.includes("flow")) {
    return [
      "```mermaid",
      "flowchart LR",
      "  A[发送] --> B{测试模式?}",
      "  B -- 是 --> C[本地伪造流]",
      "  B -- 否 --> D[WebSocket]",
      "```",
      "",
      "渲染失败时应该显示源码而不是白屏。",
    ].join("\n");
  }
  return [
    "**前端测试模式**：这条回复由浏览器生成，没有调用任何模型。",
    "",
    "它覆盖的东西：",
    "",
    "1. 分块到达的增量文本（这里是一段一段拼接的）",
    "2. 可折叠的思考过程",
    "3. 一条工具事件",
    "4. 用量与耗时页脚",
    "",
    "刷新页面就会消失，因为什么都没写进会话文件。",
  ].join("\n");
}

/** Split text into human-sized deltas so streaming looks real. */
function chunk(text: string): string[] {
  const out: string[] = [];
  let index = 0;
  while (index < text.length) {
    const size = 6 + Math.floor(Math.random() * 14);
    out.push(text.slice(index, index + size));
    index += size;
  }
  return out;
}

export interface MockTurnOptions {
  prompt: string;
  sessionId: string;
  onEvent: (event: StreamEvent) => void;
  /** Registers a timer so the caller can cancel the whole turn. */
  schedule: (fn: () => void, delay: number) => void;
}

/** Drive one fake turn through the same event handler a real stream uses. */
export function startMockTurn({
  prompt,
  sessionId,
  onEvent,
  schedule,
}: MockTurnOptions): void {
  onEvent({
    type: "start",
    stream_id: `mock-${Date.now()}`,
    session_id: sessionId,
    agent: "mock",
    workflow: "react",
    workflow_title: MOCK_WORKFLOW_TITLE,
    preset: "mock",
    model: "mock-model",
    tools: 0,
  });

  const reasoning = REASONING;
  reasoning.forEach((line, index) => {
    schedule(() => onEvent({ type: "reasoning", text: `${line}\n` }), 120 * (index + 1));
  });

  if (wantsTodoDemo(prompt)) {
    schedule(() => onEvent({ type: "todos", todos: MOCK_TODOS }), 300);
  }

  if (wantsAuditDemo(prompt)) {
    // Stop here and wait: the dialog is the rest of this turn.
    schedule(
      () =>
        onEvent({
          type: "confirm",
          request_id: `${MOCK_CONFIRM_PREFIX}${Date.now()}`,
          title: "Agent 想执行命令",
          command: "rm -rf dist && bun run build",
          cwd: "/workspace/example",
          shell: "unix/bash",
          hint: "可以修改命令后再批准；拒绝时请写清理由，它会作为工具结果返回给模型。",
          output:
            "$ bun run build\n" +
            "\x1b[32m✓\x1b[0m 161 modules transformed.\n" +
            "\x1b[33mwarning:\x1b[0m chunk larger than 500 kB\n" +
            "Build completed in 793ms\n",
          timeout: 180,
        }),
      460,
    );
    return;
  }

  if (wantsCompactDemo(prompt)) {
    // Mirrors the backend: "compacting" while the fold runs, "compacted" once
    // the summary is in the history.
    schedule(
      () =>
        onEvent({
          type: "meta",
          content: "正在压缩上下文：把较早的 48 条消息折叠成摘要，保留最近 6 条。",
          metadata: {
            type: "system",
            extra_type: "compaction_started",
            folded: 48,
            kept: 6,
          },
        }),
      260,
    );
    schedule(
      () =>
        onEvent({
          type: "meta",
          content: "已把较早的 48 条消息折叠为摘要（312 字），保留最近 6 条。",
          metadata: {
            type: "system",
            extra_type: "compaction_finished",
            folded: 48,
            kept: 6,
            summary_chars: 312,
          },
        }),
      780,
    );
  }

  if (wantsLimitDemo(prompt)) {
    // Spend a few deltas first so there is a transcript behind the card, then
    // park exactly like the backend does.
    schedule(
      () =>
        onEvent({
          type: "meta",
          content: "unix_run 被调用（仅前端）",
          metadata: { type: "function_call", function_name: "unix_run" },
        }),
      240,
    );
    schedule(
      () => onEvent({ type: "delta", text: "已经跑了几轮工具，正在收集中间结果…\n" }),
      300,
    );
    schedule(
      () =>
        onEvent({
          type: "limit",
          request_id: `${MOCK_LIMIT_PREFIX}${Date.now()}`,
          budget: 10,
          extension: 5,
          timeout: 300,
        }),
      420,
    );
    return;
  }

  // One call as the runtime reports it: a start event, then the result.
  const toolId = `mock-call-${Date.now()}`;
  schedule(
    () =>
      onEvent({
        type: "meta",
        content: "Calling function mock_tool\n",
        metadata: {
          type: "function_call",
          function_name: "mock_tool",
          is_done: false,
          tool_id: toolId,
          arguments: JSON.stringify({ path: "src/amrita_core", pattern: "TODO" }),
        },
      }),
    460,
  );
  schedule(
    () =>
      onEvent({
        type: "meta",
        content: "Called tool mock_tool\n",
        metadata: {
          type: "function_call",
          function_name: "mock_tool",
          is_done: true,
          tool_id: toolId,
          result:
            "mock_tool 的返回内容（前端伪造）：\n- 读取了 3 个文件\n- 没有发现问题",
        },
      }),
    520,
  );

  // A second thinking burst after the tool: thinking and tool calls alternate,
  // which is the whole reason they share one timeline.
  POST_TOOL_REASONING.forEach((line, index) => {
    schedule(
      () => onEvent({ type: "reasoning", text: `${line}\n` }),
      560 + index * 90,
    );
  });

  const pieces = chunk(answerFor(prompt));
  pieces.forEach((piece, index) => {
    schedule(() => onEvent({ type: "delta", text: piece }), 760 + index * 24);
  });

  const end = 800 + pieces.length * 24;
  schedule(
    () =>
      onEvent({
        type: "usage",
        usage: { prompt_tokens: 42, completion_tokens: 180, total_tokens: 222, requests: 1 },
      }),
    end,
  );
  schedule(() => onEvent(mockDone(end, 1)), end + 60);
}

/** The turn's closing frame; shared by the chat and audit demos. */
function mockDone(duration: number, toolCalls: number): StreamEvent {
  return {
    type: "done",
    usage: {
      prompt_tokens: 42,
      completion_tokens: 180,
      total_tokens: 222,
      cache_hit: null,
      cache_creation: null,
      requests: 1,
      cost: 0,
      currency: "USD",
      tokens_per_second: 40,
      duration_ms: duration,
      generation_ms: duration,
      started_at: Date.now() / 1000,
      finished_at: Date.now() / 1000,
      context_tokens: 42,
      max_context: null,
      context_percent: null,
      rounds: 1,
      steps: 0,
      tool_calls: toolCalls,
      workflow: "react",
      agent: "mock",
      preset: "mock",
      model: "mock-model",
      error: null,
    },
    totals: null,
    title: null,
    message_count: null,
    workflow: "react",
    agent: "mock",
  };
}

/**
 * Finish the budget demo after the user answers.
 *
 * Mirrors the backend: "continue" appends rounds to *this* turn, so a line says
 * so and the transcript continues; "graceful" winds down; "abort" cuts the turn
 * off without another model round — only a line saying so.
 */
export function finishMockLimit({
  onEvent,
  action,
  extension = 5,
}: {
  onEvent: (event: StreamEvent) => void;
  action: "continue" | "graceful" | "abort";
  extension?: number;
}): void {
  if (action === "abort") {
    onEvent({
      type: "delta",
      text:
        "已按你的选择中断本轮：工具调用预算用完，模型不再继续调用工具。" +
        "需要接着做时发一条新消息即可。",
    });
    onEvent(mockDone(600, 2));
    return;
  }
  onEvent({
    type: "meta",
    content:
      action === "continue"
        ? `用户选择继续迭代：本轮再追加 ${extension} 轮`
        : "按优雅收尾处理：不再调用工具",
    metadata: {
      type: "system",
      extra_type: action === "continue" ? "tool_limit_continue" : "tool_call_limit",
    },
  });
  const text =
    action === "continue"
      ? "好的，预算加上了，接着做。这是一段假的续跑输出，用来确认追加轮数之后本轮还能继续。"
      : "好的，就到这里。下面是用已有信息整理出的结论（测试模式，内容是伪造的）。";
  onEvent({ type: "delta", text });
  onEvent(mockDone(600, 2));
}

/**
 * Finish the audit demo after the user answers.
 *
 * Mirrors what the backend does with a real decision: a tool event records the
 * outcome and the answer says what happened, so the accepted/edited/rejected
 * paths all get rendered.
 */
export function finishMockAudit({
  onEvent,
  approved,
  command,
  reason,
  original,
}: {
  onEvent: (event: StreamEvent) => void;
  approved: boolean;
  command: string;
  reason: string;
  original: string;
}): void {
  const edited = approved && command.trim() !== original.trim();
  onEvent({
    type: "meta",
    content: approved
      ? `unix_run ${edited ? "（命令已被修改）" : ""} -> exit 0`
      : "unix_run 被拒绝，未执行",
    metadata: {
      type: "function_call",
      function_name: "unix_run",
      arguments: { command: approved ? command : original },
    },
  });
  const body = approved
    ? [
        `已按${edited ? "修改后的" : ""}命令执行：`,
        "",
        "```bash",
        command,
        "```",
        "",
        "因为这是前端测试模式，并没有真的运行任何东西。",
      ].join("\n")
    : [
        "**这条命令没有执行。**",
        "",
        reason ? `你的理由：${reason}` : "（没有填写理由）",
        "",
        "在真实环境里，这段理由会作为工具结果返回给模型，它会据此改方案而不是重试。",
      ].join("\n");
  for (const piece of chunk(body)) onEvent({ type: "delta", text: piece });
  onEvent(mockDone(600, 1));
}
