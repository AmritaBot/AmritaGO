/**
 * A React dialog for "type one line and confirm".
 *
 * `window.prompt` was used for the workspace path, and it is the one browser
 * dialog that cannot be styled, cannot be resized, and on some platforms is
 * suppressed outright — leaving a button that silently does nothing. This is
 * the same interaction, but as part of the app: it can autofocus, keep the
 * dialog open and show the server's complaint when the path is wrong, and it
 * matches every other dialog in the product.
 */

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function PromptDialog({
  open,
  onOpenChange,
  title,
  description,
  label,
  placeholder,
  initialValue = "",
  confirmLabel = "确定",
  /** Return a message to keep the dialog open and show it; `null` closes. */
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  /** Visible field label; also the input's accessible name. */
  label: string;
  placeholder?: string;
  initialValue?: string;
  confirmLabel?: string;
  onSubmit: (value: string) => Promise<string | null> | string | null;
}) {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset errors and paths each time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setValue(initialValue);
    setError(null);
    setBusy(false);
  }, [open, initialValue]);

  const submit = async () => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const message = await onSubmit(trimmed);
      if (message) {
        setError(message);
        return;
      }
      onOpenChange(false);
    } catch (exc) {
      setError(String(exc).replace(/^Error:\s*/, "") || "操作失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        // Focus the input because it is the dialog's primary action.
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          inputRef.current?.focus();
          inputRef.current?.select();
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && (
            <DialogDescription asChild>
              <div className="text-sm leading-relaxed text-muted-foreground">
                {description}
              </div>
            </DialogDescription>
          )}
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="prompt-dialog-input">{label}</Label>
          <Input
            id="prompt-dialog-input"
            ref={inputRef}
            value={value}
            placeholder={placeholder}
            spellCheck={false}
            autoComplete="off"
            aria-invalid={error !== null}
            onChange={(event) => {
              setValue(event.target.value);
              setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void submit();
              }
            }}
            className="font-mono text-[13px]"
          />
          {error && (
            <p className="text-xs break-all whitespace-pre-wrap text-destructive">
              {error}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={!value.trim() || busy}
          >
            {busy ? "处理中…" : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
