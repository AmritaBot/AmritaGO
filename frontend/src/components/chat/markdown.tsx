/**
 * Streaming-safe Markdown renderer.
 *
 * Two things make streaming Markdown tricky and both are handled here:
 *
 * 1. **Unterminated blocks.** While text arrives, a fenced code block or a
 *    `$$` math block is often half-written. We close dangling fences before
 *    handing the string to the parser so the tail renders as code/math instead
 *    of collapsing into a paragraph.
 * 2. **Throttled re-parsing.** The parent already coalesces deltas; Mermaid and
 *    KaTeX are the expensive parts, and they only re-run once the source
 *    settles (see ``MermaidBlock``).
 */

import {
  Children,
  createContext,
  createElement,
  lazy,
  memo,
  Suspense,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { SecretText } from "@/components/ui/secret-text";
import { CodeBlock } from "@/components/chat/code-block";
import { cn } from "@/lib/utils";

// Mermaid is ~1 MB; only pay for it when a diagram actually shows up.
const MermaidBlock = lazy(() =>
  import("./mermaid-block").then((mod) => ({ default: mod.MermaidBlock })),
);

/** Lets the fenced-code renderer know the message is still growing. */
const StreamingContext = createContext(false);

function MermaidCode({ code }: { code: string }) {
  const streaming = useContext(StreamingContext);
  return (
    <Suspense
      fallback={
        <pre className="mermaid-block font-mono text-xs whitespace-pre-wrap text-muted-foreground">
          {code}
        </pre>
      }
    >
      <MermaidBlock code={code} streaming={streaming} />
    </Suspense>
  );
}

/** A fenced block with its own toolbar; ``pre`` is skipped (see ``code``). */
function FencedCode({ className, children }: { className?: string; children?: ReactNode }) {
  const streaming = useContext(StreamingContext);
  const language = /language-([\w+#-]+)/.exec(className ?? "")?.[1] ?? "";
  if (language === "mermaid") return <MermaidCode code={String(children)} />;
  return (
    <CodeBlock
      code={String(children)}
      language={language}
      streaming={streaming}
    />
  );
}

const components: Components = {
  // ``pre`` is rendered by :class:`CodeBlock` itself, so the default wrapper
  // (which would nest a second ``pre`` around it) is suppressed here.
  pre({ children }) {
    return <>{children}</>;
  },
  code({ className, children, ...props }) {
    // A fenced block has a ``language-*`` class *or* spans lines; inline code
    // has neither. Testing the class alone would mis-style a fence written
    // without a language.
    const isFenced =
      /language-/.test(className ?? "") || String(children).includes("\n");
    if (isFenced) {
      return <FencedCode className={className}>{children}</FencedCode>;
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  a({ children, ...props }) {
    return (
      <a target="_blank" rel="noreferrer noopener" {...props}>
        {children}
      </a>
    );
  },
};

/**
 * Replace strings inside a rendered element with a masked version.
 *
 * Elements are left alone: each one that can hold text has its own entry in
 * ``maskedComponents``, so the recursion happens through the component map
 * rather than by cloning nodes here.
 */
function maskChildren(children: ReactNode): ReactNode {
  if (typeof children === "string") return <SecretText text={children} />;
  if (Array.isArray(children)) {
    return Children.map(children, (child) =>
      typeof child === "string" ? <SecretText text={child} /> : child,
    );
  }
  return children;
}

// Every element markdown can put a raw text node inside. Kept as a list so a
// new tag is one entry rather than a hand-written copy of the default renderer.
const TEXT_TAGS = [
  "p",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "td",
  "th",
  "dt",
  "dd",
  "blockquote",
  "strong",
  "em",
  "del",
] as const;

const maskedComponents: Components = {
  ...components,
  ...(Object.fromEntries(
    TEXT_TAGS.map((tag) => [
      tag,
      ({ children, node: _node, ...props }: { children?: ReactNode; node?: unknown }) =>
        createElement(tag, props, maskChildren(children)),
    ]),
  ) as Components),
  pre({ children }) {
    return <>{children}</>;
  },
  code({ className, children, ...props }) {
    const isFenced =
      /language-/.test(className ?? "") || String(children).includes("\n");
    if (isFenced) {
      return <FencedCode className={className}>{children}</FencedCode>;
    }
    return (
      <code className={className} {...props}>
        {maskChildren(children)}
      </code>
    );
  },
  a({ children, ...props }) {
    return (
      <a target="_blank" rel="noreferrer noopener" {...props}>
        {maskChildren(children)}
      </a>
    );
  },
};

/** Close an unterminated fenced block so the parser keeps the tail as code. */
function closeOpenFence(text: string): string {
  const fences = text.match(/^```/gm);
  if (!fences || fences.length % 2 === 0) return text;
  return `${text}\n\`\`\``;
}

/** Close unterminated display math delimiters. */
function closeOpenMath(text: string): string {
  const display = (text.match(/\$\$/g) ?? []).length;
  return display % 2 === 1 ? `${text}\n$$` : text;
}

export const Markdown = memo(function Markdown({
  content,
  streaming,
  className,
  maskSecrets = false,
}: {
  content: string;
  streaming?: boolean;
  className?: string;
  /** Mask credential-shaped tokens and reveal them on hover. */
  maskSecrets?: boolean;
}) {
  const prepared = useMemo(() => {
    if (!streaming) return content;
    return closeOpenMath(closeOpenFence(content));
  }, [content, streaming]);

  return (
    <StreamingContext.Provider value={Boolean(streaming)}>
      <div
        className={cn("md", streaming && "md-streaming", className)}
        data-streaming={streaming || undefined}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
          components={maskSecrets ? maskedComponents : components}
        >
          {prepared}
        </ReactMarkdown>
      </div>
    </StreamingContext.Provider>
  );
});
