/**
 * Find credential-looking tokens in free text.
 *
 * This is a display filter, not a security boundary: it stops API keys from
 * sitting in plain sight in the transcript and in screenshots of it. The value
 * is never changed on disk or in what gets sent to the model — only how the
 * bubble draws it.
 *
 * The patterns are intentionally narrow. A greedy "long random string" rule
 * would eat hashes, base64 images and UUIDs, and every false positive makes the
 * transcript harder to read.
 */

const RULES: string[] = [
  // Provider-specific prefixes, longest first so `sk-ant-…` is not read as
  // `sk-…`.
  String.raw`sk-ant-[A-Za-z0-9_-]{8,}`,
  String.raw`sk-(?:proj-)?[A-Za-z0-9_-]{8,}`,
  String.raw`github_pat_[A-Za-z0-9_]{20,}`,
  String.raw`gh[pousr]_[A-Za-z0-9]{20,}`,
  String.raw`xox[abprs]-[A-Za-z0-9-]{10,}`,
  String.raw`AKIA[0-9A-Z]{16}`,
  String.raw`AIza[0-9A-Za-z_-]{30,}`,
  String.raw`hf_[A-Za-z0-9]{20,}`,
  String.raw`glpat-[A-Za-z0-9_-]{16,}`,
  // `Authorization: Bearer …` — the scheme stays readable, the token does not.
  String.raw`Bearer\s+[A-Za-z0-9._~+/=-]{16,}`,
  // A JWT is three base64url segments; the shape is distinctive enough.
  String.raw`eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`,
];

const SECRET_RE = new RegExp(`(?:${RULES.join("|")})`, "g");

/** Split text into alternating plain strings and secret values. */
export function splitSecrets(text: string): (string | { secret: string })[] {
  if (!text) return [text];
  const parts: (string | { secret: string })[] = [];
  let last = 0;
  SECRET_RE.lastIndex = 0;
  for (const match of text.matchAll(SECRET_RE)) {
    const start = match.index ?? 0;
    if (start > last) parts.push(text.slice(last, start));
    parts.push({ secret: match[0] });
    last = start + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length > 0 ? parts : [text];
}

/**
 * What a secret looks like when hidden: keep the part that says *which* kind of
 * key it is, plus the last few characters so two keys are distinguishable.
 * Nothing else — showing a "prefix" of the secret itself defeats the purpose.
 */
export function maskSecret(value: string): string {
  const scheme = /^(Bearer\s+)/i.exec(value)?.[1] ?? "";
  const rest = value.slice(scheme.length);
  const prefix = /^(sk-ant-|sk-proj-|sk-|github_pat_|gh[pousr]_|xox[abprs]-|glpat-|AIza|AKIA|hf_)/.exec(
    rest,
  )?.[1] ?? "";
  const body = rest.slice(prefix.length);
  const tail = body.length >= 12 ? rest.slice(-4) : "";
  return `${scheme}${prefix}${"•".repeat(8)}${tail ? `…${tail}` : ""}`;
}

/** Does this text contain anything that would be masked? */
export function hasSecret(text: string): boolean {
  SECRET_RE.lastIndex = 0;
  return SECRET_RE.test(text);
}
