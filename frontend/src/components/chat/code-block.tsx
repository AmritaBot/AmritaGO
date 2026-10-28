/** Render fenced code with copy/download controls and an isolated sandboxed HTML preview. */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  MaximizeIcon,
  MinimizeIcon,
  PlayIcon,
} from "lucide-react";
import { toast } from "sonner";

import { AnsiText } from "@/components/ui/ansi-text";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { copyText } from "@/lib/clipboard";
import { highlight, type Token } from "@/lib/highlight";
import { cn } from "@/lib/utils";

/** Languages whose text is worth handing to the ANSI renderer. */
const ANSI_LANGS = new Set([
  "ansi",
  "log",
  "console",
  "terminal",
  "shell-out",
  "output",
]);

export interface CodeBlockProps {
  code: string;
  language: string;
  /** Live HTML preview is only meaningful once the fence is complete. */
  streaming?: boolean;
}

function download(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // Revoke the download URL on the next tick to avoid interrupting the download.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** A plausible filename for the block, so "下载" produces something usable. */
function filenameFor(language: string): string {
  const ext =
    {
      javascript: "js",
      typescript: "ts",
      tsx: "tsx",
      jsx: "jsx",
      python: "py",
      shell: "sh",
      bash: "sh",
      powershell: "ps1",
      json: "json",
      yaml: "yml",
      markdown: "md",
      html: "html",
      css: "css",
      sql: "sql",
    }[language.toLowerCase()] ??
    (language.toLowerCase() || "txt");
  return `snippet.${ext}`;
}

function HtmlPreviewButton({ code, title }: { code: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const surface = useRef<HTMLDivElement>(null);

  /**
   * Expand to the *screen*, not just the window.
   *
   * Requesting real fullscreen is what makes a preview usable for a layout that
   * was written for a viewport: an 80vh dialog still has the app's chrome and
   * the OS title bar around it. The dialog is maximised at the same time, so if
   * the API refuses (no user gesture, an iframe-embedded page) the result is
   * still bigger rather than unchanged.
   */
  const toggleFullscreen = async () => {
    const element = surface.current;
    if (!element) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
        setFullscreen(false);
      } else {
        await element.requestFullscreen();
        setFullscreen(true);
      }
    } catch {
      // Keep the enlarged preview when fullscreen is unavailable.
      setFullscreen((prev) => !prev);
    }
  };

  // Leaving fullscreen with Esc bypasses our click handler.
  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement !== null);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // Exit fullscreen before closing the dialog.
  useEffect(() => {
    if (open || !document.fullscreenElement) return;
    void document.exitFullscreen().catch(() => undefined);
  }, [open]);

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
        title="在沙箱里预览这段 HTML"
        onClick={() => setOpen(true)}
      >
        <PlayIcon className="size-3" />
        运行
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          ref={surface}
          className={cn(
            "flex flex-col gap-2",
            fullscreen
              ? // Anchor the fullscreen dialog to a corner to avoid double-centering.
                "top-0 left-0 h-screen w-screen max-w-none translate-x-0 translate-y-0 rounded-none border-0 p-2 shadow-none"
              : "h-[80vh] w-[min(90vw,72rem)] max-w-none",
          )}
          // Hide the dialog close button because it would cover fullscreen content.
          showClose={!fullscreen}
        >
          <DialogHeader>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <DialogTitle className="text-sm">HTML 预览</DialogTitle>
                <DialogDescription className="text-xs">
                  在 sandbox iframe 里渲染，脚本可运行但无法访问本应用的
                  Cookie、localStorage 或 DOM。
                </DialogDescription>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 shrink-0 gap-1 px-2 text-[11px]"
                title={fullscreen ? "退出全屏" : "全屏（浏览器也会全屏）"}
                onClick={() => void toggleFullscreen()}
              >
                {fullscreen ? (
                  <MinimizeIcon className="size-3.5" />
                ) : (
                  <MaximizeIcon className="size-3.5" />
                )}
                {fullscreen ? "退出全屏" : "全屏"}
              </Button>
            </div>
          </DialogHeader>
          <iframe
            title={title}
            srcDoc={code}
            // Omit allow-same-origin so untrusted preview code cannot access this origin.
            sandbox="allow-scripts allow-forms allow-modals allow-popups"
            className="min-h-0 flex-1 rounded-md border border-border bg-white"
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

export function CodeBlock({
  code,
  language,
  streaming = false,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const trimmed = useMemo(() => code.replace(/\n$/, ""), [code]);
  const label = language || "text";
  const isHtml = /^html?$/i.test(language);
  const isAnsi = ANSI_LANGS.has(language.toLowerCase());
  const tokens: Token[] = useMemo(
    () => (isAnsi ? [] : highlight(trimmed, language)),
    [isAnsi, trimmed, language],
  );
  const lines = useMemo(() => trimmed.split("\n").length, [trimmed]);
  // Collapse long code blocks to preserve space for the rest of the answer.
  const collapsible = lines > 28;

  const copy = async () => {
    if (await copyText(trimmed)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("复制失败，请手动选中");
    }
  };

  return (
    <div className="code-block group/code relative my-2 overflow-hidden rounded-lg border border-border bg-muted/50">
      <div className="flex items-center gap-1.5 border-b border-border/70 bg-muted/40 px-2.5 py-1">
        <span className="font-mono text-[11px] text-muted-foreground">
          {label}
        </span>
        {lines > 1 && (
          <span className="text-[11px] text-muted-foreground">{lines} 行</span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
            title={copied ? "已复制" : "复制这段代码"}
            onClick={() => void copy()}
          >
            {copied ? (
              <CheckIcon className="size-3 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <CopyIcon className="size-3" />
            )}
            {copied ? "已复制" : "复制"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-1.5 text-[11px] text-muted-foreground hover:text-foreground"
            title={`下载为 ${filenameFor(language)}`}
            onClick={() => download(filenameFor(language), trimmed)}
          >
            <DownloadIcon className="size-3" />
            下载
          </Button>
          {isHtml && !streaming && (
            <HtmlPreviewButton code={trimmed} title={label} />
          )}
        </div>
      </div>

      <pre
        className={cn(
          "overflow-auto bg-transparent px-3 py-2 font-mono text-[0.82rem] leading-5",
          expanded ? "max-h-[70vh]" : collapsible ? "max-h-72" : "",
        )}
      >
        <code className="!bg-transparent !p-0">
          {isAnsi ? (
            <AnsiText text={trimmed} />
          ) : (
            tokens.map((token, index) =>
              token.kind === "plain" ? (
                token.text
              ) : (
                <span key={index} className={`tok tok-${token.kind}`}>
                  {token.text}
                </span>
              ),
            )
          )}
        </code>
      </pre>

      {collapsible && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="flex w-full items-center justify-center gap-1 border-t border-border/70 bg-muted/40 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-muted"
        >
          <MaximizeIcon className="size-3" />
          {expanded ? "收起" : `展开全部（${lines} 行）`}
        </button>
      )}
    </div>
  );
}
