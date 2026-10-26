/**
 * Application version, shown next to the brand mark.
 *
 * The installed Python distribution owns the number: the backend reports it
 * once at startup and this module caches the answer. ``package.json`` stays as
 * the offline fallback, so a failed request never renders an empty badge.
 */
import pkg from "../../package.json";

import { api } from "@/lib/api";

export const APP_VERSION: string = pkg.version;

let cached: string | null = null;

/** The backend's version, fetched at most once per page load. */
export async function loadAppVersion(): Promise<string> {
  if (cached) return cached;
  try {
    const info = await api.health();
    if (typeof info.version === "string" && info.version.trim()) {
      cached = info.version;
      return cached;
    }
  } catch {
    // Do not cache fallback versions so later requests can recover the server value.
  }
  return APP_VERSION;
}
