#!/usr/bin/env bun
/**
 * Production build: bundles the SPA into ``frontend/dist``.
 *
 * Bun's HTML entrypoint emits ``index.html`` plus hashed JS/CSS chunks, which
 * is exactly the layout the FastAPI backend serves. Two Bun quirks are handled
 * explicitly:
 *
 * 1. An HTML entry whose script is ``async`` can be emitted as a *chunk*
 *    rather than an entry-point, leaving ``index.html`` pointing at an empty
 *    shell. Adding the TSX entry alongside the HTML entry guarantees a real
 *    entry-point is produced.
 * 2. ``@/*`` aliases come from ``tsconfig.json``; some Bun versions do not
 *    discover it on their own, so it is passed explicitly.
 */

import plugin from "bun-plugin-tailwind";
import { existsSync } from "node:fs";
import { copyFile, mkdir, readdir, rm } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dir);
const outdir = path.join(root, "dist");

/**
 * Copy the brand images next to the hashed bundles.
 *
 * They are copied rather than imported from TSX: the paths stay stable
 * (``/assets/brand/<name>.png``), the light/dark pair can be swapped by CSS
 * alone, and ``index.html`` can reference the favicon without the bundler
 * having to understand it. ``dist/assets`` is recreated by every build, so the
 * copy has to run after ``Bun.build`` rather than before.
 */
async function copyBrand() {
  const source = path.join(root, "src", "assets");
  const target = path.join(outdir, "assets", "brand");
  await mkdir(target, { recursive: true });
  for (const name of await readdir(source)) {
    if (!name.endsWith(".png")) continue;
    await copyFile(path.join(source, name), path.join(target, name));
  }
  console.log(`Copied brand images to ${path.relative(root, target)}`);
}

// Clean previous output, but keep user-placed static assets.
if (existsSync(outdir)) {
  for (const entry of await readdir(outdir)) {
    if (entry === "images" || entry === "public") continue;
    await rm(path.join(outdir, entry), { recursive: true, force: true });
  }
}

const start = performance.now();

const result = await Bun.build({
  entrypoints: [
    path.join(root, "index.html"),
    path.join(root, "src", "main.tsx"),
  ],
  outdir,
  plugins: [plugin],
  minify: true,
  target: "browser",
  // Enable large production source maps only when AMRITA_GO_SOURCEMAP is set.
  sourcemap: process.env.AMRITA_GO_SOURCEMAP ? "linked" : "none",
  splitting: true,
  tsconfig: path.join(root, "tsconfig.json"),
  // Put hashed assets under assets/ and keep the HTML entry at the dist root.
  naming: {
    entry: "assets/[name]-[hash].[ext]",
    chunk: "assets/[name]-[hash].[ext]",
    asset: "assets/[name]-[hash].[ext]",
  },
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
  },
});

if (!result.success) {
  console.error("Build failed:");
  for (const log of result.logs) console.error(log);
  process.exit(1);
}

/**
 * Move the emitted HTML to the dist root and normalise its asset URLs.
 *
 * Bun places the HTML entry wherever ``naming.entry`` says, and it writes the
 * script/link URLs relative to that location. The backend serves ``index.html``
 * from the dist root and mounts ``/assets``, so both the file and its URLs are
 * rewritten here.
 */
const emittedHtml = result.outputs.find((output) =>
  output.path.endsWith(".html"),
);
if (emittedHtml) {
  const target = path.join(outdir, "index.html");
  let html = await Bun.file(emittedHtml.path).text();

  // ``./foo.js`` / ``foo.js`` -> ``/assets/foo.js``
  html = html.replace(
    /(src|href)="(\.\/)?([^"\/:]+\.(?:js|css))"/g,
    (_match, attr: string, _dot: string, file: string) =>
      `${attr}="/assets/${file}"`,
  );
  // ``./assets/foo.js`` (when Bun already namespaced it) -> ``/assets/foo.js``
  html = html.replace(/(src|href)="\.\/assets\//g, '$1="/assets/');

  await Bun.write(target, html);
  if (path.resolve(emittedHtml.path) !== path.resolve(target)) {
    await rm(emittedHtml.path, { force: true });
  }
  console.log(`Wrote ${path.relative(root, target)}`);
}

await copyBrand();

// A stylesheet must exist; a missing CSS link silently produces an unstyled app.
const hasCss = result.outputs.some((output) => output.path.endsWith(".css"));
if (!hasCss) {
  console.error("No CSS was emitted - check that index.html links src/styles.css");
}

const rows = result.outputs
  .map((output) => ({
    File: path.relative(root, output.path),
    Kind: output.kind,
    Size: `${(output.size / 1024).toFixed(1)} kB`,
  }))
  .sort((a, b) => b.Size.length - a.Size.length);

console.table(rows);
console.log(`\nBuild completed in ${(performance.now() - start).toFixed(0)}ms\n`);
