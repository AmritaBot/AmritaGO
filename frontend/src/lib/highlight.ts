/**
 * A small syntax highlighter, for the code blocks in an answer.
 *
 * Why not Shiki / Prism / highlight.js: each is 200 kB–1 MB, ships its own
 * grammar engine, and would be the largest dependency in a bundle that is
 * mostly React. What a chat transcript needs is *just enough* colour to make
 * structure visible — keywords, strings, comments, numbers — not a byte-exact
 * replica of an editor theme. So this is a tokenizer of ~150 lines with a small
 * table of keywords per language family, and it degrades honestly: an unknown
 * language renders unstyled rather than wrong.
 *
 * Correctness rules that matter:
 *  - it never re-orders or drops text; concatenating the tokens returns the
 *    input, and a copy of the block is therefore always exact;
 *  - unterminated strings stop at the line end, so a half-typed string while
 *    streaming does not swallow the rest of the block;
 *  - HTML/XML is tagged by its own scanner (tags vs text), because the
 *    generic keyword approach would highlight every attribute name.
 *
 * Rendering goes through ``className`` values mapped in ``styles.css``
 * (``.tok-kw`` …), so colours follow the theme instead of being baked in.
 */

export type TokenKind =
  | "plain"
  | "comment"
  | "string"
  | "number"
  | "keyword"
  | "literal"
  | "function"
  | "tag"
  | "attr"
  | "punct"
  | "meta";

export interface Token {
  kind: TokenKind;
  text: string;
}

const COMMON_LITERALS = new Set([
  "true",
  "false",
  "null",
  "undefined",
  "None",
  "True",
  "False",
  "nil",
  "NaN",
  "Infinity",
]);

const KEYWORDS: Record<string, Set<string>> = {
  script: new Set([
    "const", "let", "var", "function", "return", "if", "else", "for", "while",
    "do", "switch", "case", "break", "continue", "new", "class", "extends",
    "super", "this", "throw", "try", "catch", "finally", "typeof", "instanceof",
    "in", "of", "async", "await", "yield", "import", "export", "from", "as",
    "default", "static", "get", "set", "interface", "type", "enum", "implements",
    "public", "private", "protected", "readonly", "declare", "namespace",
    "function", "def", "elif", "pass", "lambda", "with", "assert", "del",
    "global", "nonlocal", "raise", "except", "and", "or", "not", "is", "self",
    "fn", "impl", "struct", "trait", "pub", "use", "mod", "match", "mut",
    "package", "func", "defer", "go", "chan", "range",
  ]),
  shell: new Set([
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "function", "return", "export", "local", "readonly", "declare",
    "source", "set", "unset", "shift", "trap", "exit", "eval", "exec",
    "param", "process", "begin", "end", "foreach", "try", "catch", "finally",
    "throw", "class", "enum",
  ]),
  config: new Set(["true", "false", "null", "yes", "no", "on", "off"]),
  sql: new Set([
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET",
    "DELETE", "CREATE", "TABLE", "ALTER", "DROP", "INDEX", "JOIN", "LEFT",
    "RIGHT", "INNER", "OUTER", "ON", "GROUP", "BY", "ORDER", "HAVING", "LIMIT",
    "OFFSET", "AS", "AND", "OR", "NOT", "NULL", "PRIMARY", "KEY", "FOREIGN",
    "REFERENCES", "DEFAULT", "UNIQUE", "WITH", "UNION", "ALL", "DISTINCT",
  ]),
};

/** Which keyword table a fence language should use. */
function family(language: string): keyof typeof KEYWORDS | "html" | "css" | "none" {
  const lang = language.toLowerCase();
  // Highlight JSX/TSX as script because most of the file is code.
  if (["html", "htm", "xml", "svg", "vue"].includes(lang)) return "html";
  if (["css", "scss", "less"].includes(lang)) return "css";
  if (["sql"].includes(lang)) return "sql";
  if (
    ["json", "yaml", "yml", "toml", "ini", "env", "properties", "diff"].includes(lang)
  ) {
    return "config";
  }
  if (
    ["bash", "sh", "shell", "zsh", "console", "powershell", "ps1", "cmd", "batch"].includes(
      lang,
    )
  ) {
    return "shell";
  }
  if (
    [
      "js", "javascript", "ts", "typescript", "jsx", "tsx", "python", "py", "go",
      "rust", "rs", "java", "kotlin", "kt", "c", "cpp", "c++", "csharp", "cs",
      "ruby", "rb", "php", "swift", "scala", "lua", "dart", "elixir",
    ].includes(lang)
  ) {
    return "script";
  }
  return "none";
}

/** Identifier-ish characters; anything else splits a word. */
const WORD = /[\w$@-]/;

function push(out: Token[], kind: TokenKind, text: string) {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && last.kind === kind) last.text += text;
  else out.push({ kind, text });
}

/** Greedy scan for a C-like/script block: strings, comments, numbers, words. */
function scanScript(code: string, keywords: Set<string>): Token[] {
  const out: Token[] = [];
  let index = 0;
  while (index < code.length) {
    const rest = code.slice(index);
    const char = code[index];

    const lineComment = /^(\/\/|#(?![!{])).*/.exec(rest);
    if (lineComment && (char === "/" || char === "#")) {
      // ``#!`` shebang and ``#{`` interpolation are not comments.
      push(out, "comment", lineComment[0]);
      index += lineComment[0].length;
      continue;
    }
    if (rest.startsWith("/*")) {
      const end = rest.indexOf("*/");
      const text = end < 0 ? rest : rest.slice(0, end + 2);
      push(out, "comment", text);
      index += text.length;
      continue;
    }
    const stringStart = /^(?:[rbuf]{0,2})(["'`])/i.exec(rest);
    if (stringStart) {
      const quote = stringStart[1];
      let cursor = stringStart[0].length;
      while (cursor < rest.length) {
        if (rest[cursor] === "\\") {
          cursor += 2;
          continue;
        }
        if (rest[cursor] === quote) {
          cursor += 1;
          break;
        }
        // Stop unterminated strings at newlines to preserve following text.
        if (rest[cursor] === "\n") break;
        cursor += 1;
      }
      push(out, "string", rest.slice(0, cursor));
      index += cursor;
      continue;
    }
    const number = /^0[xXbBoO][\da-fA-F_]+|^\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest);
    if (number) {
      push(out, "number", number[0]);
      index += number[0].length;
      continue;
    }
    if (WORD.test(char)) {
      let end = index;
      while (end < code.length && WORD.test(code[end])) end += 1;
      const word = code.slice(index, end);
      const isCall = code[end] === "(";
      const kind: TokenKind = COMMON_LITERALS.has(word)
        ? "literal"
        : keywords.has(word) || keywords.has(word.toLowerCase())
          ? "keyword"
          : isCall
            ? "function"
            : "plain";
      push(out, kind, word);
      index = end;
      continue;
    }
    push(out, /[{}[\]();,.:=<>+\-*/%!&|^~?]/.test(char) ? "punct" : "plain", char);
    index += 1;
  }
  return out;
}

/** Markup: tags and attribute names get colour, text stays plain. */
function scanMarkup(code: string): Token[] {
  const out: Token[] = [];
  let index = 0;
  while (index < code.length) {
    const next = code.indexOf("<", index);
    if (next < 0) {
      push(out, "plain", code.slice(index));
      break;
    }
    push(out, "plain", code.slice(index, next));
    const close = code.indexOf(">", next);
    const end = close < 0 ? code.length : close + 1;
    const tag = code.slice(next, end);
    if (tag.startsWith("<!--")) {
      push(out, "comment", tag);
    } else {
      // Split the tag so the name and the attributes read differently.
      const match = /^<\/?\s*([\w:-]+)/.exec(tag);
      if (match) {
        push(out, "punct", tag.slice(0, match[0].length - match[1].length));
        push(out, "tag", match[1]);
        const body = tag.slice(
          match[0].length,
          tag.endsWith(">") ? tag.length - 1 : tag.length,
        );
        const attrRe = /(\s+)([\w:.-]+)(\s*=\s*)?("[^"]*"|'[^']*')?/g;
        let consumed = 0;
        let attr: RegExpExecArray | null;
        while ((attr = attrRe.exec(body)) !== null) {
          push(out, "plain", body.slice(consumed, attr.index));
          // Re-emit matched whitespace so highlighted HTML tags remain intact.
          push(out, "plain", attr[1]);
          push(out, "attr", attr[2]);
          if (attr[3]) push(out, "punct", attr[3]);
          if (attr[4]) push(out, "string", attr[4]);
          consumed = attr.index + attr[0].length;
        }
        push(out, "plain", body.slice(consumed));
        push(out, "punct", tag.endsWith(">") ? ">" : "");
      } else {
        push(out, "tag", tag);
      }
    }
    index = end;
  }
  return out;
}

/** CSS: selectors plain-ish, properties as attributes, values as literals. */
function scanCss(code: string): Token[] {
  const out: Token[] = [];
  const re = /(\/\*[\s\S]*?\*\/)|(@[\w-]+)|([\w-]+)(\s*:)/g;
  let index = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(code)) !== null) {
    push(out, "plain", code.slice(index, match.index));
    if (match[1]) push(out, "comment", match[1]);
    else if (match[2]) push(out, "meta", match[2]);
    else {
      push(out, "attr", match[3]);
      push(out, "punct", match[4]);
    }
    index = match.index + match[0].length;
  }
  push(out, "plain", code.slice(index));
  // Strings and numbers inside the remaining plain runs still deserve colour.
  const refined: Token[] = [];
  for (const token of out) {
    if (token.kind !== "plain") {
      refined.push(token);
      continue;
    }
    const inner = scanScript(token.text, new Set());
    for (const piece of inner) {
      // Keep plain runs plain; only hoist strings/numbers/comments out of them.
      if (piece.kind === "plain") push(refined, "plain", piece.text);
      else if (piece.kind === "function") push(refined, "plain", piece.text);
      else push(refined, piece.kind, piece.text);
    }
  }
  return refined;
}

/** Tokenize one block. The concatenation of ``text`` equals ``code``. */
export function highlight(code: string, language: string): Token[] {
  const kind = family(language);
  if (kind === "none") return [{ kind: "plain", text: code }];
  if (kind === "html") return scanMarkup(code);
  if (kind === "css") return scanCss(code);
  return scanScript(code, KEYWORDS[kind]);
}
