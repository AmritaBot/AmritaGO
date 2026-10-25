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

import { useMemo } from "react";
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
  for (let index = 1; index <= doc.lines; index += 1) texts.push(doc.line(index).text);

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

const theme = EditorView.theme({
  "&": { backgroundColor: "transparent", color: "inherit", height: "100%" },
  ".cm-scroller": {
    fontFamily: "var(--font-mono, ui-monospace), monospace",
    fontSize: "12px",
    lineHeight: "1.7",
  },
  ".cm-content": { padding: "10px 0" },
  ".cm-gutters": { display: "none" },
  ".cm-activeLine": { backgroundColor: "transparent" },
  "&.cm-focused": { outline: "none" },
  ".cm-preset-fence": { color: "var(--muted-foreground)", opacity: "0.7" },
  ".cm-preset-key": { color: "var(--chart-5, #3d7fd6)", fontWeight: "500" },
  ".cm-preset-value": { color: "var(--chart-4, #c26a2d)" },
  ".cm-preset-comment": { color: "var(--muted-foreground)", fontStyle: "italic" },
});

export function PresetEditor({
  value,
  onChange,
  minHeight = "0",
}: {
  value: string;
  onChange: (next: string) => void;
  minHeight?: string;
}) {
  const extensions = useMemo<Extension[]>(
    () => [
      markdown({ base: markdownLanguage }),
      syntaxHighlighting(highlight),
      frontmatterPlugin,
      theme,
    ],
    [],
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
        height="100%"
        // The component's own wrapper div is the flex child; without an
        // explicit height it grows with the document and the rounded box clips
        // it, leaving a long preset unscrollable.
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
