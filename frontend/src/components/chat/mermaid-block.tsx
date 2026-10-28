/**
 * Renders fenced ```mermaid``` blocks as diagrams.
 *
 * Nothing is rendered while the message streams: a half-written fence is rarely
 * valid Mermaid, and each failed attempt leaves a stray error node in the page.
 * The source is shown instead, and the diagram is drawn once the text settles.
 * A genuine failure is then reported, never swallowed.
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
  return DECLARATION.test(code.trim());
}

export function MermaidBlock({
  code,
  streaming,
}: {
  code: string;
  streaming?: boolean;
}) {
  const reactId = useId();
  const baseId = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;
  // Mermaid deletes the node it renders into, so the id must never be reused.
  const seq = useRef(0);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Mermaid bakes the theme into the SVG, so a theme switch needs a re-render.
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );

  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() =>
      setDark(root.classList.contains("dark")),
    );
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (streaming || !looksRenderable(code)) {
      setSvg("");
      setError(null);
      return;
    }

    let cancelled = false;
    ensureInit(dark);
    seq.current += 1;
    const renderId = `${baseId}-${seq.current}`;

    (async () => {
      try {
        const result = await mermaid.render(renderId, code);
        if (cancelled) return;
        if (!result.svg.includes("<svg"))
          throw new Error("mermaid 没有返回图形");
        setSvg(result.svg);
        setError(null);
      } catch (exc) {
        if (cancelled) return;
        // A failed render leaves its error node in the page; it must not linger.
        document.getElementById(`d${renderId}`)?.remove();
        setError(exc instanceof Error ? exc.message : String(exc));
        setSvg("");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [baseId, code, dark, streaming]);

  // An empty fence is a placeholder, not a diagram: a bordered box reads as a failure.
  if (!code.trim()) return null;

  if (error) {
    return (
      <div className="mermaid-block border-destructive/40">
        <div className="mermaid-error">{error}</div>
        <pre className="mt-2 overflow-x-auto font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {code}
        </pre>
      </div>
    );
  }

  if (!svg) {
    return (
      <pre className="mermaid-block font-mono text-xs whitespace-pre-wrap text-muted-foreground">
        {code}
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
