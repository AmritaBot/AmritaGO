/**
 * Embedded Markdown editor for agent presets.
 *
 * The file is the source of truth, so it stays one editable buffer: splitting
 * the front matter and the body into two inputs would force a re-serialize
 * that drops comments and reorders keys.
 *
 * Markdown highlighting covers the body. The front matter needs YAML colors,
 * and CodeMirror cannot nest a second language inside a byte range without a
 * parser for the whole document, so the block is decorated line by line with a
 * tiny key/value tokenizer. That is enough for the shapes a preset actually
 * uses (`key: value`, `key:`, lists, `|` blocks and `#` comments).
 */

import { useEffect, useMemo, useState } from "react";
import CodeMirror, { type Extension, EditorView } from "@uiw/react-codemirror";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { RangeSetBuilder } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view";
import { tags } from "@lezer/highlight";

const FENCE = /^---\s*$/;

/** Last line index of the front-matter block, or -1 when there is none. */
function frontmatterEnd(texts: string[]): number {
  if (!texts.length || !FENCE.test(texts[0])) return -1;
  for (let index = 1; index < texts.length; index += 1) {
    if (FENCE.test(texts[index])) return index;
  }
  return -1;
}

const fenceMark = Decoration.line({ class: "cm-preset-fence" });
const keyMark = Decoration.mark({ class: "cm-preset-key" });
const valueMark = Decoration.mark({ class: "cm-preset-value" });
const commentMark = Decoration.mark({ class: "cm-preset-comment" });

function decorateFrontmatter(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const doc = view.state.doc;
  const texts: string[] = [];
  for (let index = 1; index <= doc.lines; index += 1)
    texts.push(doc.line(index).text);

  const end = frontmatterEnd(texts);
  if (end < 0) return builder.finish();

  for (let index = 1; index <= Math.min(end, doc.lines); index += 1) {
    const line = doc.line(index);
    const text = line.text;
    if (FENCE.test(text)) {
      builder.add(line.from, line.from, fenceMark);
      continue;
    }
    if (!text.trim()) continue;

    const comment = text.indexOf("#");
    const commentStart = comment >= 0 ? line.from + comment : line.to;
    const keyMatch = /^(\s*)([A-Za-z_][\w-]*)\s*:/.exec(text);

    if (keyMatch) {
      const keyStart = line.from + keyMatch[1].length;
      builder.add(keyStart, keyStart + keyMatch[2].length, keyMark);
      const valueStart = line.from + keyMatch[0].length;
      if (commentStart > valueStart) {
        builder.add(valueStart, commentStart, valueMark);
      }
    } else if (text.trimStart().startsWith("- ")) {
      builder.add(line.from, commentStart, valueMark);
    }

    if (comment >= 0) {
      builder.add(commentStart, line.to, commentMark);
    }
  }
  return builder.finish();
}

const frontmatterPlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = decorateFrontmatter(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged) {
        this.decorations = decorateFrontmatter(update.view);
      }
    }
  },
  { decorations: (instance) => instance.decorations },
);

const highlight = HighlightStyle.define([
  { tag: tags.heading1, fontSize: "1.05em", fontWeight: "600" },
  { tag: tags.heading2, fontSize: "1em", fontWeight: "600" },
  { tag: [tags.comment, tags.lineComment], color: "var(--muted-foreground)" },
  { tag: tags.strong, fontWeight: "600" },
  { tag: tags.emphasis, fontStyle: "italic" },
  { tag: [tags.link, tags.url], textDecoration: "underline" },
  { tag: [tags.monospace, tags.string], color: "var(--chart-3, #2f9e6f)" },
]);

/**
 * The editor's colours, taken from the app's theme variables.
 *
 * ``dark`` is not cosmetic here: CodeMirror derives its base palette (selection
 * layer, caret, panels) from the theme type, and a light-typed theme inside a
 * dark page is exactly the white slab this used to render. The ``@uiw`` wrapper
 * also injects its own light theme unless told not to, so it is passed
 * ``theme="none"`` and everything visual comes from here.
 */
function editorTheme(dark: boolean) {
  return EditorView.theme(
    {
      "&": { backgroundColor: "transparent", color: "inherit", height: "100%" },
      ".cm-scroller": {
        fontFamily: "var(--font-mono, ui-monospace), monospace",
        fontSize: "12px",
        lineHeight: "1.7",
      },
      ".cm-content": { padding: "10px 0", caretColor: "var(--foreground)" },
      ".cm-gutters": { display: "none" },
      ".cm-activeLine": { backgroundColor: "transparent" },
      "&.cm-focused": { outline: "none" },
      ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--foreground)" },
      "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
        {
          backgroundColor: "color-mix(in oklab, var(--ring) 30%, transparent)",
        },
      ".cm-panels": {
        backgroundColor: "var(--card)",
        color: "var(--card-foreground)",
      },
      ".cm-tooltip": {
        backgroundColor: "var(--popover)",
        color: "var(--popover-foreground)",
        border: "1px solid var(--border)",
      },
      ".cm-preset-fence": { color: "var(--muted-foreground)", opacity: "0.7" },
      ".cm-preset-key": { color: "var(--chart-5, #3d7fd6)", fontWeight: "500" },
      ".cm-preset-value": { color: "var(--chart-4, #c26a2d)" },
      ".cm-preset-comment": {
        color: "var(--muted-foreground)",
        fontStyle: "italic",
      },
    },
    { dark },
  );
}

/**
 * The page's resolved theme, read off the root element.
 *
 * The app toggles a `dark` class from a single `useTheme` instance; a second
 * instance here would hold its own copy and drift when the user cycles themes,
 * so this watches the class the app actually applies.
 */
function useDarkMode(): boolean {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const read = () =>
      setDark(document.documentElement.classList.contains("dark"));
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);
  return dark;
}

export function PresetEditor({
  value,
  onChange,
  minHeight = "0",
}: {
  value: string;
  onChange: (next: string) => void;
  minHeight?: string;
}) {
  const dark = useDarkMode();
  const extensions = useMemo<Extension[]>(
    () => [
      markdown({ base: markdownLanguage }),
      syntaxHighlighting(highlight),
      frontmatterPlugin,
      editorTheme(dark),
    ],
    [dark],
  );

  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card focus-within:border-ring/60"
      style={{ minHeight }}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={extensions}
        // ``none`` disables the wrapper's built-in light theme; see editorTheme.
        theme="none"
        height="100%"
        // Fill the flex container so long presets remain scrollable.
        style={{ height: "100%", minHeight: 0 }}
        basicSetup={{
          lineNumbers: false,
          foldGutter: false,
          highlightActiveLine: false,
          highlightActiveLineGutter: false,
          bracketMatching: true,
          autocompletion: false,
          searchKeymap: false,
        }}
      />
    </div>
  );
}
