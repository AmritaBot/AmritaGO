import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";

import { TooltipProvider } from "./components/ui/tooltip";
import App from "./App";
import "./styles.css";

/**
 * Tab icon, one file per OS colour scheme.
 *
 * Created here rather than written into ``index.html``: the brand files are
 * copied to ``/assets/brand/`` by the build, and Bun's HTML entrypoint treats
 * every ``<link href>`` as an asset to bundle, which fails on an absolute path.
 */
function installFavicons() {
  const variants: Array<[media: string, file: string]> = [
    ["(prefers-color-scheme: light)", "amrita-black.png"],
    ["(prefers-color-scheme: dark)", "amrita-white.png"],
  ];
  for (const [media, file] of variants) {
    const link = document.createElement("link");
    link.rel = "icon";
    link.type = "image/png";
    link.media = media;
    link.href = `/assets/brand/${file}`;
    document.head.append(link);
  }
}

installFavicons();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TooltipProvider>
      <App />
      <Toaster position="top-center" richColors closeButton />
    </TooltipProvider>
  </StrictMode>,
);
