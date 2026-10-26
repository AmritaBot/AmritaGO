/**
 * Bun dev server: bundles on the fly, proxies /api to the FastAPI backend, and
 * bridges the chat WebSocket so the browser can talk to the same origin.
 *
 *   bun --hot src/index.ts          # development
 *   NODE_ENV=production bun src/index.ts
 *
 * The production layout is served by FastAPI itself (frontend/dist); this
 * server exists so the UI can be developed without rebuilding, and so the
 * WebSocket has a same-origin proxy during development.
 */

import path from "node:path";
import { copyFile, mkdir, readdir } from "node:fs/promises";

const API_TARGET = process.env.AMRITA_API_TARGET ?? "http://127.0.0.1:8000";
const ROOT = path.resolve(import.meta.dir, "..");
const DEV_OUT = path.join(ROOT, ".dev-build");
const PORT = Number(process.env.AMRITA_FRONTEND_PORT ?? "5173");

type UpgradeData = { url: string };

const bridges = new Map<import("bun").ServerWebSocket<UpgradeData>, WebSocket>();

/** Cached dev build: ``{ html, builtAt }``. */
let devBuild: { html: string; builtAt: number } | null = null;
/** Single in-flight build; concurrent requests await the same promise. */
let building: Promise<Response> | null = null;

/**
 * Bridge a browser socket to the backend.
 *
 * The browser's own reconnect loop already retries with backoff, so this
 * handler must NOT reconnect by itself: doing so would spin a tight loop while
 * the backend is down. It only mirrors the backend's state onto the browser
 * socket and closes it so the client can schedule its own retry.
 */
function bridge(ws: import("bun").ServerWebSocket<UpgradeData>) {
  const target = API_TARGET.replace(/^http/, "ws") + ws.data.url;
  let backend: WebSocket;
  try {
    backend = new WebSocket(target);
  } catch (error) {
    console.error(`[ws] cannot open ${target}: ${String(error)}`);
    closeQuietly(ws, 1011, "backend unreachable");
    return;
  }
  bridges.set(ws, backend);

  backend.addEventListener("message", (event) => {
    if (ws.readyState === WebSocket.OPEN) ws.send(String(event.data));
  });
  backend.addEventListener("close", (event) => {
    bridges.delete(ws);
    closeQuietly(ws, event.code || 1000, event.reason);
  });
  backend.addEventListener("error", () => {
    // ``close`` follows immediately; nothing to log beyond the reason.
  });
}

function closeQuietly(
  ws: import("bun").ServerWebSocket<UpgradeData>,
  code: number,
  reason: string,
) {
  if (ws.readyState === WebSocket.OPEN) {
    try {
      ws.close(code, reason);
    } catch {
      // Already closing.
    }
  }
}

const server = Bun.serve<UpgradeData>({
  port: PORT,
  async fetch(req, server) {
    const url = new URL(req.url);

    // Proxy WebSockets locally so the browser uses one origin.
    if (url.pathname === "/api/ws/chat") {
      const ok = server.upgrade(req, { data: { url: url.pathname + url.search } });
      return ok ? undefined : new Response("Upgrade failed", { status: 400 });
    }

    if (url.pathname.startsWith("/api/")) {
      return proxy(req, url);
    }

    // Serve cached dev assets directly instead of rebuilding per request.
    if (url.pathname.startsWith("/assets/")) {
      const asset = Bun.file(path.join(DEV_OUT, url.pathname.slice("/".length)));
      if (await asset.exists()) {
        return new Response(asset, {
          headers: { "Cache-Control": "no-store" },
        });
      }
    }

    return renderIndex();
  },
  websocket: {
    open(ws) {
      bridge(ws);
    },
    message(ws, message) {
      const backend = bridges.get(ws);
      if (backend?.readyState === WebSocket.OPEN) backend.send(String(message));
    },
    close(ws) {
      const backend = bridges.get(ws);
      bridges.delete(ws);
      if (backend?.readyState === WebSocket.OPEN) backend.close();
    },
  },
  development: process.env.NODE_ENV !== "production" && {
    hmr: true,
    console: true,
  },
});

/**
 * Serve the SPA.
 *
 * The bundle is built once at startup (and again on an explicit rebuild) rather
 * than per request: rebuilding on every page load made first paint as slow as a
 * full production build, which is not what a dev server should cost.
 */
async function renderIndex(): Promise<Response> {
  if (devBuild) return htmlResponse(devBuild.html);
  if (building) return building;
  building = buildOnce().finally(() => {
    building = null;
  });
  return building;
}

async function buildOnce(): Promise<Response> {
  const result = await Bun.build({
    // Add the TSX entry so index.html references a real bundle entry.
    entrypoints: [path.join(ROOT, "index.html"), path.join(ROOT, "src", "main.tsx")],
    outdir: DEV_OUT,
    plugins: [(await import("bun-plugin-tailwind")).default],
    target: "browser",
    sourcemap: "inline",
    tsconfig: path.join(ROOT, "tsconfig.json"),
    naming: {
      entry: "assets/[name]-[hash].[ext]",
      chunk: "assets/[name]-[hash].[ext]",
      asset: "assets/[name]-[hash].[ext]",
    },
    define: { "process.env.NODE_ENV": JSON.stringify("development") },
  });
  if (!result.success) {
    const detail = result.logs.map(String).join("\n");
    console.error(detail);
    return new Response(`<pre>${detail}</pre>`, {
      status: 500,
      headers: { "Content-Type": "text/html;charset=utf-8" },
    });
  }

  const emitted = result.outputs.find((output) => output.path.endsWith(".html"));
  if (!emitted) return new Response("index.html missing from build", { status: 500 });

  // Rewrite URLs to absolute /assets so deep routes still resolve them.
  let html = await emitted.text();
  html = html.replace(
    /(src|href)="(\.\/)?([^"\/]+\.(?:js|css))"/g,
    (_m, attr: string, _d: string, file: string) => `${attr}="/assets/${file}"`,
  );

  devBuild = { html, builtAt: Date.now() };
  await copyBrand();
  console.log("dev bundle ready");
  return htmlResponse(html);
}

/**
 * Mirror the brand images into ``.dev-build`` so ``/assets/brand/*.png``
 * resolves identically here and in the production build.
 */
async function copyBrand() {
  const source = path.join(ROOT, "src", "assets");
  const target = path.join(DEV_OUT, "assets", "brand");
  await mkdir(target, { recursive: true });
  for (const name of await readdir(source)) {
    if (!name.endsWith(".png")) continue;
    await copyFile(path.join(source, name), path.join(target, name));
  }
}

function htmlResponse(html: string): Response {
  return new Response(html, {
    headers: { "Content-Type": "text/html;charset=utf-8", "Cache-Control": "no-store" },
  });
}

/** Forward an API request to the backend, preserving streamed bodies. */
async function proxy(req: Request, url: URL): Promise<Response> {
  const target = new URL(url.pathname + url.search, API_TARGET);
  const headers = new Headers();
  for (const name of ["content-type", "accept", "authorization"]) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body:
        req.method === "GET" || req.method === "HEAD"
          ? undefined
          : await req.arrayBuffer(),
      redirect: "manual",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: new Headers(upstream.headers),
    });
  } catch (error) {
    return Response.json(
      { detail: `backend unreachable: ${String(error)}` },
      { status: 502 },
    );
  }
}

console.log(`AmritaAgent dev server: ${server.url}`);
console.log(`/api proxied to ${API_TARGET}`);

// Pre-build for a fast first load and report failures on the next request.
renderIndex()
  .then(() => console.log("ready"))
  .catch((error) => console.error("initial build failed:", error));
