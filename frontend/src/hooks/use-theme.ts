import { useCallback, useEffect, useState } from "react";

/**
 * Three choices, not two: an explicit light, an explicit dark, and "follow the
 * desktop". The last one is the default — someone who has set their OS to dark
 * should not be shown a white page because the app never asked.
 */
export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const KEY = "amrita-go-theme";
const LEGACY_KEY = "amrita-agent-theme";
const QUERY = "(prefers-color-scheme: dark)";

function readStored(): Theme {
  const stored = localStorage.getItem(KEY) ?? localStorage.getItem(LEGACY_KEY);
  if (stored === "light" || stored === "dark" || stored === "system") {
    // Migrate the pre-rename key once, then forget it.
    if (localStorage.getItem(KEY) === null) localStorage.setItem(KEY, stored);
    if (localStorage.getItem(LEGACY_KEY) !== null) localStorage.removeItem(LEGACY_KEY);
    return stored;
  }
  return "system";
}

function prefersDark(): boolean {
  return window.matchMedia(QUERY).matches;
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(readStored);
  const [systemDark, setSystemDark] = useState<boolean>(prefersDark);

  // Watch the media query so system-theme changes apply without a reload.
  useEffect(() => {
    const media = window.matchMedia(QUERY);
    const handler = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    setSystemDark(media.matches);
    media.addEventListener("change", handler);
    return () => media.removeEventListener("change", handler);
  }, []);

  const resolved: ResolvedTheme =
    theme === "system" ? (systemDark ? "dark" : "light") : theme;

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", resolved === "dark");
    root.style.colorScheme = resolved;
    localStorage.setItem(KEY, theme);
  }, [theme, resolved]);

  const cycle = useCallback(() => {
    setTheme((prev) => (prev === "light" ? "dark" : prev === "dark" ? "system" : "light"));
  }, []);

  return { theme, resolved, setTheme, cycle };
}
