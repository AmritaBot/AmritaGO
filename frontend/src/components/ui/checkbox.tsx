import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A native checkbox rather than a Radix one.
 *
 * The tool tree needs three states (all / some / none) per group, and
 * `indeterminate` only exists as a DOM property — a native input lets us set it
 * directly through a ref, keeps keyboard behaviour and screen-reader semantics
 * for free, and avoids pulling in another dependency for a 16px box.
 */
function Checkbox({
  className,
  indeterminate = false,
  ...props
}: React.ComponentProps<"input"> & { indeterminate?: boolean }) {
  const ref = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      ref={ref}
      type="checkbox"
      data-slot="checkbox"
      className={cn(
        "size-4 shrink-0 cursor-pointer rounded-[3px] border border-input accent-primary",
        "focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40",
        "disabled:cursor-not-allowed disabled:opacity-50",
        "max-sm:size-5",
        className,
      )}
      {...props}
    />
  );
}

export { Checkbox };
