/**
 * The plan panel.
 *
 * A long task survives only if the plan is visible while it is being executed.
 * The agent writes the list with ``todo_write`` / ``todo_update``; this shows it
 * above the composer so the user can see progress at a glance and notice when
 * the plan and the answer have drifted apart.
 *
 * Collapsed state is sticky per browser: some people want the list always,
 * others want it once. Neither should have to toggle it every turn.
 */

import { useEffect, useState } from "react";
import {
  CheckCircle2Icon,
  ChevronDownIcon,
  CircleIcon,
  ListTodoIcon,
  Trash2Icon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { TodoList } from "@/lib/api";
import { cn } from "@/lib/utils";

const COLLAPSE_KEY = "amrita:todos-collapsed";

export function TodoPanel({
  todos,
  onClear,
}: {
  todos: TodoList | null;
  onClear?: () => void;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      // Storage can be unavailable (private mode); the panel still works.
    }
  }, [collapsed]);

  if (!todos || todos.items.length === 0) return null;

  const percent = Math.round((todos.done / todos.total) * 100);

  return (
    <div className="mx-auto w-full max-w-3xl px-4">
      <section
        aria-label="待办事项"
        className="mb-1 overflow-hidden rounded-lg border border-border/70 bg-card/80"
      >
        <div className="flex items-center gap-2 px-3 py-1.5">
          <ListTodoIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <button
            type="button"
            onClick={() => setCollapsed((prev) => !prev)}
            aria-expanded={!collapsed}
            className="flex min-w-0 flex-1 items-center gap-2 text-left"
          >
            <span className="truncate text-xs font-medium">{todos.title}</span>
            <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
              {todos.done}/{todos.total}
            </span>
            <span
              aria-hidden
              className="hidden h-1 min-w-16 flex-1 overflow-hidden rounded-full bg-muted sm:block"
            >
              <span
                className="block h-full rounded-full bg-emerald-500 transition-[width]"
                style={{ width: `${percent}%` }}
              />
            </span>
            <ChevronDownIcon
              className={cn(
                "size-3.5 shrink-0 text-muted-foreground transition-transform",
                collapsed && "-rotate-90",
              )}
            />
          </button>
          {onClear && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="size-6 text-muted-foreground"
              aria-label="清除计划"
              title="清除计划"
              onClick={onClear}
            >
              <Trash2Icon className="size-3" />
            </Button>
          )}
        </div>

        {!collapsed && (
          <ol className="max-h-44 overflow-y-auto border-t border-border/60 px-3 py-1.5">
            {todos.items.map((item) => (
              <li
                key={item.id}
                className="flex items-start gap-2 py-[3px] text-xs leading-relaxed"
              >
                {item.done ? (
                  <CheckCircle2Icon className="mt-[2px] size-3.5 shrink-0 text-emerald-500" />
                ) : (
                  <CircleIcon className="mt-[2px] size-3.5 shrink-0 text-muted-foreground" />
                )}
                <span
                  className={cn(
                    "text-foreground",
                    item.done && "text-muted-foreground line-through",
                  )}
                >
                  {item.text}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
