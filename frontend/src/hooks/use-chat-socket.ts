/**
 * Chat socket hook.
 *
 * Transport: one WebSocket per session. The server owns the run and appends
 * every event to a replayable buffer, so this hook can treat the socket as a
 * *viewer*:
 *
 * - If the socket dies mid-answer the run keeps going. We reconnect with the
 *   session id and the server replays the buffer from byte 0 — no lost text,
 *   no duplicated text (a `start` event resets the local assembly).
 * - Incoming deltas are coalesced before touching React state. A fast provider
 *   can emit hundreds of tiny chunks per second; re-rendering Markdown on each
 *   of them is what makes streaming UIs stutter. We buffer for a short window
 *   and flush once, which is the "merge chunks when the UI is slower than the
 *   stream" behaviour.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  api,
  type ConfirmRequest,
  type RunningTool,
  type StreamEvent,
  type TodoList,
  type ToolLimitRequest,
  type TurnUsage,
  type UsageTotals,
  wsUrl,
} from "@/lib/api";
import {
  finishMockAudit,
  finishMockLimit,
  isMockConfirm,
  isMockLimit,
  isMockMode,
  startMockTurn,
} from "@/lib/mock-stream";

export interface LiveUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  requests: number;
}

/**
 * One step of the turn, in arrival order.
 *
 * ``reasoning`` and ``event`` are kept apart from ``content``/``events`` so the
 * transcript can render thinking and tool calls as one timeline: the order is
 * the information, and separate buckets lose it.
 */
export type TraceEntry =
  | { kind: "reasoning"; text: string }
  | { kind: "event"; content: string; metadata: Record<string, unknown> };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  reasoning: string;
  events: { content: string; metadata: Record<string, unknown> }[];
  /** Thinking and tool steps interleaved, exactly as they arrived. */
  trace: TraceEntry[];
  streaming: boolean;
  error?: string;
  usage?: TurnUsage | null;
  live?: LiveUsage | null;
  /**
   * Milliseconds this turn spent inside reasoning chunks.
   *
   * Measured in the browser, not reported by the provider: a reasoning stream
   * arrives as ordinary chunks with no timing metadata, and what the user wants
   * to know is "how long did it think", which only the client can observe.
   */
  thinkingMs?: number;
  attachments?: {
    id: string;
    kind: string;
    name: string;
    url?: string | null;
    path?: string | null;
  }[];
  createdAt: number;
}

export interface RunPlan {
  agent: string;
  workflow: string;
  workflowTitle: string;
  preset: string;
  model: string;
  tools: number;
}

export type ConnectionState = "connecting" | "open" | "closed";

const RECONNECT_DELAYS = [400, 800, 1500, 2500, 4000, 6000];

/** Coalescing window: flush at most ~22 times per second. */
const FLUSH_INTERVAL_MS = 45;
/** Hard cap so a huge burst never waits for the interval. */
const MAX_PENDING_CHARS = 1200;

interface Pending {
  text: string;
  reasoning: string;
}

/** Extend the open reasoning step, or start a new one after a tool step. */
function appendReasoning(trace: TraceEntry[], text: string): TraceEntry[] {
  const last = trace.at(-1);
  if (last?.kind === "reasoning") {
    return [...trace.slice(0, -1), { kind: "reasoning", text: last.text + text }];
  }
  return [...trace, { kind: "reasoning", text }];
}

export function useChatSocket(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("closed");
  const [busy, setBusy] = useState(false);
  const [streamId, setStreamId] = useState<string | null>(null);
  const [plan, setPlan] = useState<RunPlan | null>(null);
  const [liveUsage, setLiveUsage] = useState<LiveUsage | null>(null);
  const [totals, setTotals] = useState<UsageTotals | null>(null);
  const [lastUsage, setLastUsage] = useState<TurnUsage | null>(null);
  //: A tool is parked waiting for the user; the dialog blocks on this.
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequest | null>(null);
  //: The run spent its tool budget and is parked until this is answered.
  const [limitRequest, setLimitRequest] = useState<ToolLimitRequest | null>(null);
  //: Tool calls the server is executing right now, keyed by ``tool_id``. Only
  //: these may be cancelled; anything else has already finished.
  const [runningTools, setRunningTools] = useState<Record<string, RunningTool>>({});
  const [todos, setTodos] = useState<TodoList | null>(null);
  //: Messages the next request will carry. Comes from the usage endpoint on
  //: load and from each finished turn, so a reload and a live run agree.
  const [contextMessages, setContextMessages] = useState<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const firstDeltaRef = useRef<number | null>(null);
  //: When the currently-running reasoning interval opened, and the total spent
  //: reasoning before it. Reasoning and answer chunks interleave, so the total
  //: is the sum of intervals rather than first-chunk-to-last-chunk.
  const thinkStartRef = useRef<number | null>(null);
  const thinkTotalRef = useRef(0);

  const socketRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<Pending>({ text: "", reasoning: "" });
  const timerRef = useRef<number | null>(null);
  //: Pending timers of a front-end test-mode turn, so ``stop()`` can cancel it.
  const mockTimers = useRef<number[]>([]);
  const activeIdRef = useRef<string | null>(null);
  const streamIdRef = useRef<string | null>(null);
  const reconnectAttempt = useRef(0);
  const manualClose = useRef(false);
  const sessionRef = useRef<string | null>(sessionId);
  const retryRef = useRef<number | null>(null);
  //: Mirror of ``messages`` so a callback can read the latest list without
  //: capturing it (and without re-creating the callback on every delta).
  const messagesRef = useRef<ChatMessage[]>([]);

  sessionRef.current = sessionId;
  messagesRef.current = messages;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const clearMock = useCallback(() => {
    for (const id of mockTimers.current) window.clearTimeout(id);
    mockTimers.current = [];
  }, []);

  /** Close the open reasoning interval, if any. */
  const settleThinking = useCallback(() => {
    if (thinkStartRef.current === null) return;
    thinkTotalRef.current += Date.now() - thinkStartRef.current;
    thinkStartRef.current = null;
  }, []);

  /** Total thinking time so far, including the interval still open. */
  const thinkingMs = useCallback(
    () =>
      thinkTotalRef.current +
      (thinkStartRef.current === null ? 0 : Date.now() - thinkStartRef.current),
    [],
  );

  /** Apply the coalesced buffer to the active assistant message. */
  const flush = useCallback(() => {
    timerRef.current = null;
    const pending = pendingRef.current;
    const id = activeIdRef.current;
    if (!id || (!pending.text && !pending.reasoning)) return;
    const { text, reasoning } = pending;
    pendingRef.current = { text: "", reasoning: "" };
    const thinking = thinkingMs();
    setMessages((prev) =>
      prev.map((message) =>
        message.id === id
          ? {
              ...message,
              content: message.content + text,
              reasoning: message.reasoning + reasoning,
              trace: reasoning
                ? appendReasoning(message.trace, reasoning)
                : message.trace,
              thinkingMs: thinking > 0 ? thinking : message.thinkingMs,
            }
          : message,
      ),
    );
  }, [thinkingMs]);

  const scheduleFlush = useCallback(() => {
    if (timerRef.current !== null) return;
    timerRef.current = window.setTimeout(flush, FLUSH_INTERVAL_MS);
  }, [flush]);

  const pushDelta = useCallback(
    (text: string, kind: "text" | "reasoning" = "text") => {
      const pending = pendingRef.current;
      if (kind === "text") pending.text += text;
      else pending.reasoning += text;
      // A single huge chunk should not sit in the buffer for the whole window.
      if (pending.text.length + pending.reasoning.length >= MAX_PENDING_CHARS) {
        clearTimer();
        flush();
        return;
      }
      scheduleFlush();
    },
    [clearTimer, flush, scheduleFlush],
  );

  const ensureAssistant = useCallback(() => {
    if (activeIdRef.current) return activeIdRef.current;
    const id = crypto.randomUUID();
    activeIdRef.current = id;
    setMessages((prev) => [
      ...prev,
      {
        id,
        role: "assistant",
        content: "",
        reasoning: "",
        events: [],
        trace: [],
        streaming: true,
        createdAt: Date.now() / 1000,
      },
    ]);
    return id;
  }, []);

  const finalizeAssistant = useCallback(
    (patch?: Partial<ChatMessage>) => {
      flush();
      settleThinking();
      const total = thinkTotalRef.current;
      thinkTotalRef.current = 0;
      const id = activeIdRef.current;
      activeIdRef.current = null;
      setBusy(false);
      if (!id) return;
      setMessages((prev) =>
        prev.map((message) =>
          message.id === id
            ? {
                ...message,
                ...patch,
                thinkingMs: total > 0 ? total : message.thinkingMs,
                streaming: false,
              }
            : message,
        ),
      );
      // Drop an assistant bubble that never produced anything (e.g. a preset
      // error right after send) unless it carries an error or a notice: a turn
      // cut off by the budget gate has nothing but the notice to show.
      setMessages((prev) =>
        prev.filter(
          (message) =>
            !(
              message.role === "assistant" &&
              !message.streaming &&
              !message.content &&
              !message.error &&
              message.events.length === 0
            ),
        ),
      );
    },
    [flush, settleThinking],
  );

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      switch (event.type) {
        case "start": {          streamIdRef.current = event.stream_id;
          setStreamId(event.stream_id);
          setPlan({
            agent: event.agent,
            workflow: event.workflow,
            workflowTitle: event.workflow_title,
            preset: event.preset,
            model: event.model,
            tools: event.tools,
          });
          setLiveUsage(null);
          setRunningTools({});
          startedAtRef.current = Date.now();
          firstDeltaRef.current = null;
          // A repeated `start` means the server replayed the buffer from the
          // beginning: rebuild this bubble instead of appending twice. The
          // user bubble may also already be on screen (either optimistic or
          // from history), so drop a duplicate trailing user message.
          const id = activeIdRef.current;
          pendingRef.current = { text: "", reasoning: "" };
          clearTimer();
          setMessages((prev) => {
            const base = [...prev];
            const last = base.at(-1);
            const before = base.at(-2);
            if (
              last?.role === "user" &&
              before?.role === "user" &&
              last.content === before.content
            ) {
              base.pop();
            }
            if (!id) return base;
            return base.map((message) =>
              message.id === id
                ? {
                    ...message,
                    content: "",
                    reasoning: "",
                    events: [],
                    trace: [],
                    streaming: true,
                    error: undefined,
                  }
                : message,
            );
          });
          if (!id) ensureAssistant();
          setBusy(true);
          break;
        }
        case "delta": {
          ensureAssistant();
          // The answer started (or resumed): whatever ran before it was thinking.
          settleThinking();
          if (firstDeltaRef.current === null) firstDeltaRef.current = Date.now();
          pushDelta(event.text, "text");
          break;
        }
        case "reasoning": {
          ensureAssistant();
          if (thinkStartRef.current === null) thinkStartRef.current = Date.now();
          pushDelta(event.text, "reasoning");
          break;
        }
        case "meta": {
          ensureAssistant();
          flush();
          const id = activeIdRef.current;
          const payload = { content: event.content, metadata: event.metadata };
          setMessages((prev) =>
            prev.map((message) =>
              message.id === id
                ? {
                    ...message,
                    events: [...message.events, payload],
                    trace: [
                      ...message.trace,
                      { kind: "event", content: event.content, metadata: event.metadata },
                    ],
                  }
                : message,
            ),
          );
          break;
        }
        case "usage": {
          ensureAssistant();
          setLiveUsage(event.usage);
          break;
        }
        case "confirm": {
          // The turn is blocked server-side until this is answered, so the
          // dialog is the only way forward — not merely a notification.
          setPendingConfirm({
            request_id: event.request_id,
            title: event.title,
            command: event.command,
            cwd: event.cwd,
            shell: event.shell,
            hint: event.hint,
            output: event.output,
            timeout: event.timeout,
          });
          break;
        }
        case "todos": {
          setTodos(event.todos);
          break;
        }
        case "confirm_closed": {
          // The call behind the prompt was cancelled or the turn stopped: the
          // question is gone, so the dialog must go with it.
          setPendingConfirm((prev) =>
            prev && prev.request_id === event.request_id ? null : prev,
          );
          break;
        }
        case "tool_running": {
          // A cancel button is only truthful while the server still holds the
          // call, so this event opens the window and ``tool_finished`` closes it.
          setRunningTools((prev) => ({
            ...prev,
            [event.tool_id]: {
              tool_id: event.tool_id,
              tool: event.tool,
              timeout: event.timeout,
              started_at: event.started_at,
            },
          }));
          break;
        }
        case "tool_finished": {
          setRunningTools((prev) => {
            if (!(event.tool_id in prev)) return prev;
            const next = { ...prev };
            delete next[event.tool_id];
            return next;
          });
          break;
        }
        case "limit": {
          // The run is parked inside the tool loop until this is answered, so a
          // timeout winds it down rather than leaving it hanging.
          settleThinking();
          setLimitRequest({
            request_id: event.request_id,
            budget: event.budget,
            extension: event.extension,
            timeout: event.timeout,
          });
          break;
        }
        case "error": {
          flush();
          // An error before any delta (a bad preset, say) has no bubble yet;
          // show it on its own so the user sees why nothing happened.
          const id = activeIdRef.current ?? ensureAssistant();
          setMessages((prev) =>
            prev.map((message) =>
              message.id === id
                ? { ...message, error: event.message, streaming: false }
                : message,
            ),
          );
          activeIdRef.current = null;
          setBusy(false);
          setRunningTools({});
          break;
        }
        case "done": {
          // A run that failed mid-way still ends with ``done`` (the server
          // publishes it to terminate the stream); don't clear the error.
          finalizeAssistant({ usage: event.usage ?? null, live: null });
          setLiveUsage(null);
          setRunningTools({});
          if (event.usage?.context_messages != null) {
            setContextMessages(event.usage.context_messages);
          }
          // A gate cannot outlive its run.
          setLimitRequest(null);
          setLastUsage(event.usage ?? null);
          // A parked confirmation cannot outlive its turn; the server already
          // resolved it (answered, timed out or cancelled).
          setPendingConfirm(null);
          // A gate cannot outlive its run either, and an aborted one never got
          // an answer at all.
          setLimitRequest(null);
          if (event.totals) {
            setTotals(event.totals);
            // The sidebar keeps its own copy of the session list; tell it the
            // counters moved instead of refetching on every chunk.
            window.dispatchEvent(
              new CustomEvent("amrita:usage", {
                detail: { sessionId: sessionRef.current, totals: event.totals },
              }),
            );
          }
          if (event.title) {
            window.dispatchEvent(
              new CustomEvent("amrita:session-title", {
                detail: { sessionId: sessionRef.current, title: event.title },
              }),
            );
          }
          break;
        }
        case "pong":
          break;
        default:
          break;
      }
    },
    [
      clearTimer,
      ensureAssistant,
      finalizeAssistant,
      flush,
      pushDelta,
      settleThinking,
    ],
  );

  const connect = useCallback(() => {
    const id = sessionRef.current;
    if (!id) return;
    manualClose.current = false;
    const socket = new WebSocket(wsUrl(id));
    socketRef.current = socket;
    setConnection("connecting");

    socket.onopen = () => {
      reconnectAttempt.current = 0;
      setConnection("open");
    };

    socket.onmessage = (event) => {
      try {
        handleEvent(JSON.parse(event.data) as StreamEvent);
      } catch {
        // A malformed frame must not kill the socket; the server never sends
        // one, but a proxy could.
      }
    };

    socket.onclose = () => {
      setConnection("closed");
      if (manualClose.current) return;
      if (sessionRef.current !== id) return;
      const delay =
        RECONNECT_DELAYS[
          Math.min(reconnectAttempt.current, RECONNECT_DELAYS.length - 1)
        ];
      reconnectAttempt.current += 1;
      retryRef.current = window.setTimeout(connect, delay);
    };

    socket.onerror = () => {
      // ``onclose`` follows; nothing to do but let the retry run.
    };
  }, [handleEvent]);

  const disconnect = useCallback(() => {
    manualClose.current = true;
    if (retryRef.current !== null) {
      window.clearTimeout(retryRef.current);
      retryRef.current = null;
    }
    socketRef.current?.close();
    socketRef.current = null;
    setConnection("closed");
  }, []);

  /** Load persisted history, then open the socket. */
  const loadSession = useCallback(
    async (id: string) => {
      clearTimer();
      pendingRef.current = { text: "", reasoning: "" };
      activeIdRef.current = null;
      streamIdRef.current = null;
      setStreamId(null);
      setBusy(false);
      setLiveUsage(null);
      setPendingConfirm(null);
      setRunningTools({});
      if (isMockMode()) {
        // A test turn is never persisted, so there is no history to load and
        // keeping an older transcript on screen would be misleading.
        setMessages([]);
        return;
      }
      // The plan is state of its own: it outlives individual turns, so reload
      // it from disk rather than relying on the stream replaying it.
      api
        .getTodos(id)
        .then((list) => setTodos(list.total > 0 ? list : null))
        .catch(() => setTodos(null));
      try {
        const [{ entries }, usage] = await Promise.all([
          api.history(id),
          api.usage(id).catch(() => null),
        ]);
        setTotals(usage?.totals ?? null);
        setLastUsage(usage?.turns.at(-1) ?? null);
        setContextMessages(usage?.context?.messages ?? null);
        setMessages(
          entries
            .filter((entry) => entry.role === "user" || entry.role === "assistant")
            .map((entry) => ({
              id: entry.id,
              role: entry.role as "user" | "assistant",
              content: entry.content,
              reasoning: entry.reasoning ?? "",
              events: entry.events ?? [],
              // The transcript stores the reasoning blob and the events in
              // separate fields, so their interleaving is gone by the time a
              // session is reloaded; "reasoning, then steps" is the only order
              // the stored data can justify.
              trace: [
                ...(entry.reasoning
                  ? [{ kind: "reasoning" as const, text: entry.reasoning }]
                  : []),
                ...(entry.events ?? []).map((item) => ({
                  kind: "event" as const,
                  content: item.content,
                  metadata: item.metadata,
                })),
              ],
              streaming: false,
              error: entry.error ?? undefined,
              usage: entry.usage ?? null,
              attachments: entry.attachments ?? [],
              createdAt: entry.created_at,
            })),
        );
      } catch {
        setMessages([]);
      }
    },
    [clearTimer],
  );

  /**
   * Re-read the session from disk and re-open the socket.
   *
   * The old version only re-fetched ``history.jsonl``, which is written at the
   * *end* of a turn — so pressing it mid-run, or right after a finished turn,
   * changed nothing on screen and looked broken. Reconnecting matters just as
   * much: the server replays an unfinished run's buffer from offset 0, and that
   * is the only way to recover from a client that missed frames.
   */
  const reload = useCallback(async () => {
    if (!sessionId) return;
    await loadSession(sessionId);
    if (isMockMode()) return;
    manualClose.current = false;
    reconnectAttempt.current = 0;
    disconnect();
    connect();
  }, [connect, disconnect, loadSession, sessionId]);

  // Reset + reconnect whenever the session changes.
  useEffect(() => {    disconnect();
    if (!sessionId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      await loadSession(sessionId);
      if (cancelled) return;
      if (isMockMode()) {
        setConnection("open");
        return;
      }
      connect();
    })();
    return () => {
      cancelled = true;
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);
  // If the user was away while a run was in flight, the socket replays the
  // buffer on connect — but only when the page regains focus do we know the
  // difference between "idle" and "still running".
  useEffect(() => {
    const onFocus = () => {
      const socket = socketRef.current;
      if (!sessionId) return;
      if (isMockMode()) return;
      if (!socket || socket.readyState === WebSocket.CLOSED) {
        reconnectAttempt.current = 0;
        connect();
      }
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [connect, sessionId]);

  const send = useCallback(
    (
      message: string,
      attachments: ChatMessage["attachments"] = [],
      model?: string | null,
      access?: string | null,
    ) => {
      const text = message.trim();
      if ((!text && attachments.length === 0) || !sessionId || busy) return false;
      const mock = isMockMode();
      const socket = socketRef.current;
      if (!mock && (!socket || socket.readyState !== WebSocket.OPEN)) {
        connect();
        return false;
      }

      // Optimistic user bubble; the server appends its own copy to history.
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "user",
          content: text,
          reasoning: "",
          events: [],
          trace: [],
          streaming: false,
          attachments,
          createdAt: Date.now() / 1000,
        },
      ]);
      pendingRef.current = { text: "", reasoning: "" };
      activeIdRef.current = null;
      // A new turn starts a new measurement; the previous turn's total was
      // already copied onto its bubble.
      thinkStartRef.current = null;
      thinkTotalRef.current = 0;
      setBusy(true);
      startedAtRef.current = Date.now();
      firstDeltaRef.current = null;

      if (mock) {
        // Nothing leaves the browser: the fake stream drives the same event
        // handler, so every rendering path is exercised for real.
        clearMock();
        startMockTurn({
          prompt: text,
          sessionId,
          onEvent: handleEvent,
          schedule: (fn, delay) => {
            mockTimers.current.push(window.setTimeout(fn, delay));
          },
        });
        return true;
      }

      socket!.send(
        JSON.stringify({
          type: "send",
          session_id: sessionId,
          message: text,
          attachments,
          model: model ?? undefined,
          // The server narrows this against the preset's own `access` list, so
          // sending it is a request, not a grant.
          access: access ?? undefined,
        }),
      );
      return true;
    },
    [busy, clearMock, connect, handleEvent, sessionId],
  );

  const stop = useCallback(() => {
    clearMock();
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN && sessionId) {
      socket.send(JSON.stringify({ type: "stop", session_id: sessionId }));
    }
    finalizeAssistant();
    streamIdRef.current = null;
    setRunningTools({});
  }, [clearMock, finalizeAssistant, sessionId]);

  /**
   * Re-run the prompt behind a failed turn.
   *
   * The failed bubble is dropped and the *previous user message* is sent again,
   * rather than asking the server to regenerate: the history already holds that
   * exchange, and a retry is a new turn everywhere else in the app too, so a
   * silent rewrite would describe a conversation that never happened.
   *
   * Returns ``false`` when there is nothing to retry, or when the socket is down
   * (``send`` already kicks off a reconnect, so the button stays usable).
   */
  const retry = useCallback(() => {
    const list = messagesRef.current;
    let failed = -1;
    for (let index = list.length - 1; index >= 0; index -= 1) {
      if (list[index].role === "assistant" && list[index].error) {
        failed = index;
        break;
      }
    }
    if (failed < 0) return false;

    let prompt: ChatMessage | null = null;
    for (let index = failed - 1; index >= 0; index -= 1) {
      if (list[index].role === "user") {
        prompt = list[index];
        break;
      }
    }
    if (!prompt || (!prompt.content && !prompt.attachments?.length)) return false;

    const failedId = list[failed].id;
    setMessages((prev) => prev.filter((message) => message.id !== failedId));
    return send(prompt.content, prompt.attachments ?? []);
  }, [send]);

  /**
   * Answer the parked tool call.
   *
   * The edited command is always sent: the dialog may have rewritten it, and
   * the backend compares it against the original to decide whether the model
   * should be told its command was changed.
   */
  const sendConfirm = useCallback(
    (approved: boolean, command?: string, reason?: string) => {
      const request = pendingConfirm;
      if (!request) return;
      setPendingConfirm(null);
      if (isMockConfirm(request.request_id)) {
        // Test mode: the fabricated request is answered in-browser so the
        // accepted / edited / rejected paths are all reachable without a shell.
        finishMockAudit({
          onEvent: handleEvent,
          approved,
          command: command ?? request.command,
          reason: reason ?? "",
          original: request.command,
        });
        return;
      }
      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN && sessionId) {
        socket.send(
          JSON.stringify({
            type: "confirm_response",
            session_id: sessionId,
            request_id: request.request_id,
            approved,
            command: command ?? request.command,
            reason: reason ?? "",
          }),
        );
      }
    },
    [handleEvent, pendingConfirm, sessionId],
  );

  /** Answer the budget gate: continue, wind down, or cut the turn off. */
  const resolveLimit = useCallback(
    (action: "continue" | "graceful" | "abort") => {
      const request = limitRequest;
      if (!request) return;
      setLimitRequest(null);
      if (isMockLimit(request.request_id)) {
        // Test mode: the fabricated gate is answered in-browser, so every
        // branch is reachable without burning a real tool budget.
        finishMockLimit({
          onEvent: handleEvent,
          action,
          extension: request.extension,
        });
        return;
      }
      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN && sessionId) {
        socket.send(
          JSON.stringify({
            type: "limit_response",
            session_id: sessionId,
            request_id: request.request_id,
            action,
          }),
        );
        return;
      }
      // The socket died while the run was parked: the gate times out on its own
      // and winds down, so there is nothing else to send. Say so rather than
      // letting the click look like it worked.
      toast.error("连接已断开，无法回复；本轮会自动收尾");
    },
    [handleEvent, limitRequest, sessionId],
  );

  /**
   * Cancel one running tool call.
   *
   * The turn keeps going: the server hands the reason to the model as the
   * tool's result, so it can choose another approach instead of the whole run
   * dying mid-loop.
   */
  const cancelTool = useCallback(
    (toolId: string, reason: string) => {
      if (isMockMode()) {
        // Test mode has no server to ask; drop the local entry so the button
        // disappears the way a real ``tool_finished`` would make it.
        setRunningTools((prev) => {
          const next = { ...prev };
          delete next[toolId];
          return next;
        });
        return true;
      }
      const socket = socketRef.current;
      if (!socket || socket.readyState !== WebSocket.OPEN || !sessionId) {
        toast.error("连接已断开，无法取消这个工具调用");
        return false;
      }
      socket.send(
        JSON.stringify({
          type: "tool_cancel",
          session_id: sessionId,
          tool_id: toolId,
          reason,
        }),
      );
      return true;
    },
    [sessionId],
  );

  /** Forget the plan locally and on disk. */
  const clearTodos = useCallback(() => {
    setTodos(null);
    if (sessionId) void api.clearTodos(sessionId).catch(() => undefined);
  }, [sessionId]);

  return useMemo(
    () => ({
      messages,
      connection,
      busy,
      streamId,
      plan,
      liveUsage,
      totals,
      lastUsage,
      contextMessages,
      pendingConfirm,
      todos,
      send,
      stop,
      retry,
      limitRequest,
      resolveLimit,
      sendConfirm,
      runningTools,
      cancelTool,
      clearTodos,
      reload,
    }),
    [
      messages,
      connection,
      busy,
      streamId,
      plan,
      liveUsage,
      totals,
      lastUsage,
      contextMessages,
      pendingConfirm,
      todos,
      send,
      stop,
      retry,
      limitRequest,
      resolveLimit,
      sendConfirm,
      runningTools,
      cancelTool,
      clearTodos,
      reload,
    ],
  );
}
