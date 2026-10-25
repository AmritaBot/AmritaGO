/**
 * Session list with an archive drawer.
 *
 * Deleting is only offered inside the archive. A live session instead gets
 * branch and archive: both are reversible, while deletion should require
 * opening the archive first — that is the whole point of having one.
 *
 * Archive and delete both ask once more before they run: the buttons are
 * hover-revealed icons on a row that is itself clickable, so a stray click on
 * the wrong row is easy, and a side bar is not the place to lose a transcript.
 */

import { useState } from "react";
import {
  ArchiveIcon,
  ArchiveRestoreIcon,
  ChevronDownIcon,
  GitBranchIcon,
  MessageSquareIcon,
  PlusIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { SessionMeta } from "@/lib/api";
import { cn, relativeTime } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";

function SessionRow({
  session,
  active,
  archived,
  onSelect,
  onRename,
  onArchive,
  onDelete,
  onBranch,
}: {
  session: SessionMeta;
  active: boolean;
  archived: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onArchive: () => void;
  onDelete: () => void;
  onBranch: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter") onSelect();
      }}
      className={cn(
        "group flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "hover:bg-sidebar-accent/60",
        archived && "opacity-80",
      )}
    >
      {session.parent_id ? (
        <GitBranchIcon className="size-4 shrink-0 opacity-80" />
      ) : (
        <MessageSquareIcon className="size-4 shrink-0 opacity-80" />
      )}
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            onBlur={() => {
              const title = draft.trim();
              if (title && title !== session.title) onRename(title);
              setEditing(false);
            }}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") setEditing(false);
            }}
            className="w-full rounded-sm border border-border bg-background px-1.5 py-0.5 text-sm outline-none"
          />
        ) : (
          <div
            className="truncate"
            onDoubleClick={(event) => {
              event.stopPropagation();
              setEditing(true);
              setDraft(session.title);
            }}
            title={session.title}
          >
            {session.title}
          </div>
        )}
        <div className="truncate text-[11px] text-muted-foreground">
          {relativeTime(session.updated_at)}
          {session.usage.total_tokens > 0
            ? ` · ${session.usage.total_tokens} tok`
            : ""}
          {session.archived ? " · 已归档" : ""}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
        {archived ? (
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={(event) => {
                event.stopPropagation();
                onArchive();
              }}
              aria-label="取消归档"
            >
              <ArchiveRestoreIcon className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={(event) => {
                event.stopPropagation();
                onDelete();
              }}
              aria-label="删除会话"
            >
              <Trash2Icon className="size-3.5" />
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={(event) => {
                event.stopPropagation();
                onBranch();
              }}
              aria-label="从此会话创建分支"
            >
              <GitBranchIcon className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={(event) => {
                event.stopPropagation();
                onArchive();
              }}
              aria-label="归档会话"
            >
              <ArchiveIcon className="size-3.5" />
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

export function SessionSidebar({
  sessions,
  archived,
  activeId,
  onSelect,
  onCreate,
  onDelete,
  onRename,
  onArchive,
  onBranch,
  onOpenSettings,
  version = APP_VERSION,
}: {
  sessions: SessionMeta[];
  archived: SessionMeta[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onArchive: (id: string, archived: boolean) => void;
  onBranch: (session: SessionMeta) => void;
  onOpenSettings: () => void;
  /** Backend-reported version; falls back to the bundle's own. */
  version?: string;
}) {
  const [showArchive, setShowArchive] = useState(false);
  //: The session waiting for a second click, plus which action it was for.
  const [pending, setPending] = useState<{
    session: SessionMeta;
    kind: "archive" | "delete";
  } | null>(null);

  return (
    <>
      <div className="flex h-14 items-center justify-between border-b border-sidebar-border px-3">
        {/*
          Text only: the mark lives in the conversation area, where it has room
          to be the mark instead of a favicon next to a heading. Repeating it in
          the corner only ever made it smaller.
        */}
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[15px] font-semibold tracking-tight">
            AmritaGO
          </div>
          <div
            className="truncate font-mono text-[10px] text-muted-foreground"
            title={`版本 ${version}`}
          >
            v{version}
          </div>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onCreate} aria-label="新建会话">
          <PlusIcon />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-2">
        <p className="px-2 pt-1 pb-2 text-[11px] font-medium tracking-wider text-muted-foreground uppercase">
          会话
        </p>
        <ul className="flex flex-col gap-0.5">
          {sessions.map((session) => (
            <li key={session.id}>
              <SessionRow
                session={session}
                active={session.id === activeId}
                archived={false}
                onSelect={() => onSelect(session.id)}
                onRename={(title) => onRename(session.id, title)}
                onArchive={() => setPending({ session, kind: "archive" })}
                onDelete={() => setPending({ session, kind: "delete" })}
                onBranch={() => onBranch(session)}
              />
            </li>
          ))}
          {sessions.length === 0 && (
            <li className="px-2 py-1 text-xs text-muted-foreground">还没有会话</li>
          )}
        </ul>

        <button
          type="button"
          onClick={() => setShowArchive((prev) => !prev)}
          className="mt-3 flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-[11px] font-medium tracking-wider text-muted-foreground uppercase transition-colors hover:bg-sidebar-accent/60"
        >
          <ArchiveIcon className="size-3" />
          归档 {archived.length > 0 ? `(${archived.length})` : ""}
          <ChevronDownIcon
            className={cn(
              "ml-auto size-3 transition-transform",
              showArchive && "rotate-180",
            )}
          />
        </button>
        {showArchive && (
          <ul className="mt-1 flex flex-col gap-0.5">
            {archived.map((session) => (
              <li key={session.id}>
                <SessionRow
                  session={session}
                  active={session.id === activeId}
                  archived
                  onSelect={() => onSelect(session.id)}
                  onRename={(title) => onRename(session.id, title)}
                  // Restoring is the undo of the action that needed confirming,
                  // so it runs straight away.
                  onArchive={() => onArchive(session.id, false)}
                  onDelete={() => setPending({ session, kind: "delete" })}
                  onBranch={() => onBranch(session)}
                />
              </li>
            ))}
            {archived.length === 0 && (
              <li className="px-2 py-1 text-xs text-muted-foreground">归档为空</li>
            )}
          </ul>
        )}
      </div>

      <div className="border-t border-sidebar-border p-2">
        <Button
          variant="ghost"
          className="w-full justify-start gap-2"
          onClick={onOpenSettings}
        >
          <SettingsIcon />
          设置
        </Button>
      </div>

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) setPending(null);
        }}
        title={
          pending?.kind === "delete" ? "永久删除这个会话？" : "把这个会话归档？"
        }
        description={
          pending === null ? null : pending.kind === "delete" ? (
            <>
              「{pending.session.title}」的消息、用量记录会一起删掉，无法恢复。
            </>
          ) : (
            <>
              「{pending.session.title}」会从会话列表移到「归档」，
              <br />
              内容不变，随时可以取消归档。
            </>
          )
        }
        confirmLabel={pending?.kind === "delete" ? "永久删除" : "归档"}
        destructive={pending?.kind === "delete"}
        onConfirm={() => {
          if (pending === null) return;
          if (pending.kind === "delete") onDelete(pending.session.id);
          else onArchive(pending.session.id, true);
        }}
      />
    </>
  );
}
