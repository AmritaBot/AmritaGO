/**
 * Minimal ANSI (SGR) renderer for terminal output.
 *
 * Shell output is full of colour codes, and dumping the raw bytes into a `<pre>`
 * shows `ESC[32m` noise instead of a green string. This parses just the SGR
 * family — the one terminals actually use for colour — and drops every other
 * escape sequence, including the OSC prompt markers the shell layer already
 * consumes.
 *
 * Deliberately small: no cursor movement, no screens, no 256-colour palette
 * tables beyond the xterm 16 + the 256-cube. If a program emits a full-screen
 * TUI it will render as its final frame's text, which is the honest thing to
 * show in a read-only viewer.
 */

import { Fragment, type ReactNode } from "react";

/** Style accumulated from SGR parameters. */
interface SgrState {
  fg: string | null;
  bg: string | null;
  bold: boolean;
  dim: boolean;
  italic: boolean;
  underline: boolean;
  inverse: boolean;
}

const EMPTY: SgrState = {
  fg: null,
  bg: null,
  bold: false,
  dim: false,
  italic: false,
  underline: false,
  inverse: false,
};

/** The standard 16 ANSI colours, in a palette that reads on dark and light. */
const BASIC = [
  "#1f2328",
  "#d1242f",
  "#1a7f37",
  "#9a6700",
  "#0969da",
  "#8250df",
  "#1b7c83",
  "#6e7781",
];

const BASIC_BRIGHT = [
  "#6e7781",
  "#ff7b72",
  "#3fb950",
  "#d29922",
  "#58a6ff",
  "#bc8cff",
  "#39c5cf",
  "#b1bac4",
];

/** xterm's 256-colour cube, computed only when a program asks for it. */
function colour256(index: number): string | null {
  if (index < 0 || index > 255) return null;
  if (index < 8) return BASIC[index];
  if (index < 16) return BASIC_BRIGHT[index - 8];
  if (index < 232) {
    const n = index - 16;
    const steps = [0, 95, 135, 175, 215, 255];
    const r = steps[Math.floor(n / 36) % 6];
    const g = steps[Math.floor(n / 6) % 6];
    const b = steps[n % 6];
    return rgb(r, g, b);
  }
  const grey = 8 + (index - 232) * 10;
  return rgb(grey, grey, grey);
}

function rgb(r: number, g: number, b: number): string {
  const clamp = (value: number) =>
    Math.max(0, Math.min(255, Math.round(value)));
  return `rgb(${clamp(r)} ${clamp(g)} ${clamp(b)})`;
}

function applySgr(state: SgrState, params: number[]): SgrState {
  const next = { ...state };
  for (let index = 0; index < params.length; index += 1) {
    const code = params[index];
    if (code === 0) {
      Object.assign(next, EMPTY);
    } else if (code === 1) {
      next.bold = true;
    } else if (code === 2) {
      next.dim = true;
    } else if (code === 3) {
      next.italic = true;
    } else if (code === 4) {
      next.underline = true;
    } else if (code === 7) {
      next.inverse = true;
    } else if (code === 22) {
      next.bold = false;
      next.dim = false;
    } else if (code === 23) {
      next.italic = false;
    } else if (code === 24) {
      next.underline = false;
    } else if (code === 27) {
      next.inverse = false;
    } else if (code >= 30 && code <= 37) {
      next.fg = BASIC[code - 30];
    } else if (code >= 90 && code <= 97) {
      next.fg = BASIC_BRIGHT[code - 90];
    } else if (code === 39) {
      next.fg = null;
    } else if (code >= 40 && code <= 47) {
      next.bg = BASIC[code - 40];
    } else if (code >= 100 && code <= 107) {
      next.bg = BASIC_BRIGHT[code - 100];
    } else if (code === 49) {
      next.bg = null;
    } else if (code === 38 || code === 48) {
      // Extended colour: 38;5;n (cube) or 38;2;r;g;b (truecolor).
      const target = code === 38 ? "fg" : "bg";
      const mode = params[index + 1];
      if (mode === 5) {
        next[target] = colour256(params[index + 2]);
        index += 2;
      } else if (mode === 2) {
        next[target] = rgb(
          params[index + 2],
          params[index + 3],
          params[index + 4],
        );
        index += 4;
      }
    }
  }
  return next;
}

interface Segment {
  text: string;
  style: SgrState;
}

const SGR = /\u001b\[([0-9;]*)m/g;
/**
 * Any other CSI/FE sequence, plus OSC blocks, are dropped.
 *
 * The final byte must exclude ``m`` (0x6d): that is the SGR terminator, and
 * stripping it here would delete the colours before they are parsed.
 */
const OTHER_ESCAPES =
  /\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b\[[0-9;?]*[\u0040-\u006c\u006e-\u007e]|\u001b[@-Z\\-_]/g;

/** Split raw terminal text into styled runs. */
export function parseAnsi(raw: string): Segment[] {
  const segments: Segment[] = [];
  let style = EMPTY;
  let cursor = 0;
  let pending = "";

  const flush = () => {
    if (!pending) return;
    segments.push({ text: pending, style });
    pending = "";
  };

  SGR.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SGR.exec(raw)) !== null) {
    pending += raw.slice(cursor, match.index);
    flush();
    const params = match[1]
      .split(";")
      .map((part) => (part === "" ? 0 : Number.parseInt(part, 10)));
    style = applySgr(style, params.some(Number.isNaN) ? [0] : params);
    cursor = match.index + match[0].length;
  }
  pending += raw.slice(cursor);
  flush();
  return segments;
}

function styleToCss(style: SgrState): React.CSSProperties | undefined {
  const fg = style.inverse ? style.bg : style.fg;
  const bg = style.inverse ? style.fg : style.bg;
  const css: React.CSSProperties = {};
  if (fg) css.color = fg;
  if (bg) css.backgroundColor = bg;
  if (style.bold) css.fontWeight = 600;
  if (style.dim) css.opacity = 0.65;
  if (style.italic) css.fontStyle = "italic";
  if (style.underline) css.textDecoration = "underline";
  return Object.keys(css).length > 0 ? css : undefined;
}

/**
 * Render terminal text with colour.
 *
 * Returns plain text when nothing styles it, so the common case adds no DOM.
 */
export function AnsiText({
  text,
  className,
}: {
  text: string;
  className?: string;
}): ReactNode {
  const clean = text.replace(OTHER_ESCAPES, "").replace(/\r\n?/g, "\n");
  const segments = parseAnsi(clean);
  const styled = segments.some(
    (segment) =>
      segment.style.fg ||
      segment.style.bg ||
      segment.style.bold ||
      segment.style.dim ||
      segment.style.italic ||
      segment.style.underline ||
      segment.style.inverse,
  );
  if (!styled) {
    // Strip consumed ANSI codes when they produced no styling.
    return (
      <span className={className}>{segments.map((s) => s.text).join("")}</span>
    );
  }
  return (
    <span className={className}>
      {segments.map((segment, index) => (
        <Fragment key={index}>
          <span style={styleToCss(segment.style)}>{segment.text}</span>
        </Fragment>
      ))}
    </span>
  );
}

/** True when the text carries escape sequences worth rendering. */
export function hasAnsi(text: string): boolean {
  return /\u001b\[/.test(text);
}
