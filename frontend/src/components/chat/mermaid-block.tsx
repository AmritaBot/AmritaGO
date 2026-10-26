/**
 * Renders fenced ```mermaid``` blocks as diagrams.
 *
 * While a message streams, the fence is usually incomplete; Mermaid throws on
 * partial sources. So:
 *
 * - nothing is rendered until the source looks like a diagram declaration;
 * - while streaming we debounce, and a parse error simply keeps the previous
 *   diagram (never flashes an error at the user mid-answer);
 * - once streaming stops, failures are shown so a genuinely broken diagram is
 *   visible instead of silently swallowed.
 */

import mermaid from "mermaid";
import { useEffect, useId, useRef, useState } from "react";

let configured = false;
let configuredForDark = false;

function ensureInit(dark: boolean) {
  if (configured && configuredForDark === dark) return;
  configured = true;
  configuredForDark = dark;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    theme: dark ? "dark" : "default",
    fontFamily: "inherit",
    flowchart: { htmlLabels: true, curve: "basis" },
    sequence: { mirrorActors: false },
  });
}

const DECLARATION =
  /^\s*(graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(-v2)?|erDiagram|journey|gantt|pie|gitGraph|mindmap|timeline|quadrantChart|requirementDiagram|C4Context|block-beta|sankey-beta|xychart-beta)\b/;

function looksRenderable(code: string): boolean {
  const trimmed = code.trim();
  if (!trimmed) return false;
  return DECLARATION.test(trimmed);
}

/** Streaming render debounce: Mermaid is slow, so settle first. */
const SETTLE_MS = 350;

export function MermaidBlock({
  code,
  streaming,
}: {
  code: string;
  streaming?: boolean;
}) {
  const reactId = useId();
  const renderId = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [settled, setSettled] = useState(code);
  const lastGood = useRef("");

  useEffect(() => {
    if (!streaming) {
      setSettled(code);
      return;
    }
    const timer = window.setTimeout(() => setSettled(code), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [code, streaming]);

  useEffect(() => {
    if (!looksRenderable(settled)) {
      setSvg("");
      setError(null);
      lastGood.current = "";
      return;
    }

    let cancelled = false;
    const dark = document.documentElement.classList.contains("dark");
    ensureInit(dark);

    (async () => {
      try {
        const result = await mermaid.render(renderId, settled);
        if (cancelled) return;
        setSvg(result.svg);
        setError(null);
        lastGood.current = settled;
      } catch (exc) {
        if (cancelled) return;
        // Ignore parse failures while the diagram is still streaming.
        if (!streaming) {
          setError(exc instanceof Error ? exc.message : String(exc));
          setSvg("");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [settled, streaming, renderId]);

  if (error) {
    return (
      <div className="mermaid-block border-destructive/40">
        <div className="mermaid-error">{error}</div>
        <pre className="mt-2 overflow-x-auto font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {settled}
        </pre>
      </div>
    );
  }

  if (!svg) {
    return (
      <pre className="mermaid-block font-mono text-xs whitespace-pre-wrap text-muted-foreground">
        {settled}
      </pre>
    );
  }

  return (
    <div
      className="mermaid-block"
      // Mermaid returns sanitized SVG (securityLevel: "strict").
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
