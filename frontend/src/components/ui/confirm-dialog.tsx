/**
 * A yes/no dialog for actions that should not happen on a single misclick.
 *
 * Archive and delete both live as small icon buttons on a hover-revealed row,
 * which is exactly where a stray click lands. The dialog is the second step:
 * it names what is about to happen and, when the action cannot be undone, says
 * so instead of relying on the button's tooltip.
 */

import { CircleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  cancelLabel = "取消",
  /** Red confirm button and a warning icon; for irreversible actions. */
  destructive = false,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {destructive && (
              <CircleAlertIcon className="size-4 shrink-0 text-destructive" />
            )}
            {title}
          </DialogTitle>
          <DialogDescription asChild>
            <div className="text-sm leading-relaxed text-muted-foreground">
              {description}
            </div>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={() => {
              onOpenChange(false);
              onConfirm();
            }}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
