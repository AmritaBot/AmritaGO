/**
 * Clipboard helper.
 *
 * `navigator.clipboard` only exists in a secure context: `https://` or
 * `http://localhost` / `127.0.0.1`. A tool like this is routinely opened from
 * another machine (`http://192.168.x.x:8000`), where the modern API is simply
 * absent and every copy button would look broken. The textarea + `execCommand`
 * path still works there, so it is kept as a fallback rather than an error.
 */

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through: permission denied or a non-secure context.
  }

  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}
