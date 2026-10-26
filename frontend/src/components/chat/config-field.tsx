/**
 * One derived form field, shared by the AmritaCore config editor and the
 * per-plugin config editor.
 *
 * Both backends describe their settings the same way (a flattened Pydantic
 * model: path, type, choices, constraints), so a single renderer keeps the two
 * screens visually identical and means a new field type is implemented once.
 */

import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { CoreConfigField } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface FieldError {
  field: string;
  message: string;
}

export function ConfigField({
  field,
  value,
  error,
  onChange,
  idPrefix = "config",
}: {
  field: CoreConfigField;
  value: unknown;
  error?: FieldError;
  onChange: (value: unknown) => void;
  idPrefix?: string;
}) {
  const id = `${idPrefix}-${field.path.replace(/\./g, "-")}`;
  const invalid = Boolean(error);
  const { min, max, exclusive_min, exclusive_max } = field.constraints;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <label htmlFor={id} className="text-sm" title={field.doc}>
            {field.label}
            {field.unsafe && (
              <span className="ml-1.5 rounded bg-amber-500/15 px-1 py-0.5 align-middle text-[10px] font-medium text-amber-700 dark:text-amber-400">
                unsafe
              </span>
            )}
            {field.source === "default" && (
              // Explain that the key is absent, so this value comes from the library default.
              <span
                className="ml-1.5 rounded bg-muted px-1 py-0.5 align-middle text-[10px] font-medium text-muted-foreground"
                title="配置文件里没有这一项，显示的是当前 AmritaCore 的默认值"
              >
                默认
              </span>
            )}
          </label>
          <p className="font-mono text-[11px] text-muted-foreground">{field.path}</p>
          {field.description && (
            <p className="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
          )}
        </div>

        <div className="shrink-0">
          {field.type === "boolean" ? (
            <Switch
              id={id}
              checked={Boolean(value)}
              onCheckedChange={(checked) => onChange(checked)}
            />
          ) : field.type === "literal" && field.choices ? (
            <select
              id={id}
              value={String(value ?? "")}
              onChange={(event) => onChange(event.target.value)}
              className={cn(
                "h-8 rounded-md border bg-background px-2 text-sm outline-none",
                invalid ? "border-destructive" : "border-input",
              )}
            >
              {field.choices.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <Input
              id={id}
              type={field.type === "string" ? "text" : "number"}
              step={field.type === "number" ? 0.05 : undefined}
              min={min ?? exclusive_min}
              max={max ?? exclusive_max}
              value={value === null || value === undefined ? "" : String(value)}
              aria-invalid={invalid}
              onChange={(event) => {
                const raw = event.target.value;
                if (field.type === "string") {
                  onChange(raw);
                  return;
                }
                onChange(raw === "" ? null : Number(raw));
              }}
              className={cn("w-40", invalid && "border-destructive")}
            />
          )}
        </div>
      </div>
      {field.unsafe && field.unsafe_reason && (
        <p className="text-[11px] text-amber-700 dark:text-amber-400">
          影响：{field.unsafe_reason}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error.message}</p>}
    </div>
  );
}

/** Pick the error that belongs to ``path`` (or to one of its children). */
export function errorFor(errors: FieldError[], path: string): FieldError | undefined {
  return errors.find((err) => err.field === path || err.field.startsWith(`${path}.`));
}

/**
 * A ``PUT`` failure carries ``{detail: {message, errors, current_hash}}`` in the
 * response body; the client surfaces the body as ``Error.message``, so it has
 * to be parsed back out for per-field display.
 */
export function parseConfigError(error: unknown): {
  message: string;
  errors: FieldError[];
} {
  const raw = error instanceof Error ? error.message : String(error);
  try {
    const parsed = JSON.parse(raw) as {
      detail?: { message?: string; errors?: FieldError[] };
    };
    return {
      message: parsed.detail?.message ?? "保存失败",
      errors: parsed.detail?.errors ?? [],
    };
  } catch {
    return { message: `保存失败：${raw}`, errors: [] };
  }
}
