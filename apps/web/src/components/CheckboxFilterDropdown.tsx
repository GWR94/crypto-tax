import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type CheckboxFilterItem = {
  id: string;
  label: string;
  count?: number;
  icon?: ReactNode;
  description?: string;
};

function FilterRow({
  checked,
  onChange,
  label,
  count,
  icon,
  description,
  bold = false,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  count?: number;
  icon?: ReactNode;
  description?: string;
  bold?: boolean;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/50",
        bold ? "items-center border-b border-border pb-2 font-medium" : "items-start"
      )}
      onClick={(e) => e.stopPropagation()}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        className={cn("h-3.5 w-3.5 shrink-0 rounded border-input accent-primary", description && "mt-0.5")}
      />
      {icon ? <span className={cn("shrink-0", description && "mt-0.5")}>{icon}</span> : null}
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            "flex items-center justify-between gap-2",
            !checked && "text-muted-foreground line-through"
          )}
        >
          <span className="truncate">{label}</span>
          {count !== undefined ? (
            <span className="shrink-0 tabular-nums text-xs text-muted-foreground no-underline">
              {count}
            </span>
          ) : null}
        </span>
        {description ? (
          <span className="mt-0.5 block text-xs leading-snug text-muted-foreground no-underline">
            {description}
          </span>
        ) : null}
      </span>
    </label>
  );
}

export function CheckboxFilterDropdown({
  allLabel,
  items,
  hiddenIds,
  onToggleAll,
  onToggleItem,
  className,
  menuClassName,
}: {
  allLabel: string;
  items: CheckboxFilterItem[];
  hiddenIds: ReadonlySet<string>;
  onToggleAll: () => void;
  onToggleItem: (id: string) => void;
  className?: string;
  menuClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const allVisible = hiddenIds.size === 0;
  const visibleCount = items.length - hiddenIds.size;

  const triggerLabel = useMemo(() => {
    if (allVisible) return allLabel;
    if (visibleCount === 0) return `${allLabel} (none)`;
    return `${allLabel} (${visibleCount}/${items.length})`;
  }, [allLabel, allVisible, visibleCount, items.length]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex h-9 w-full min-w-[130px] items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-sm",
          "hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          !allVisible && "border-primary/40"
        )}
      >
        <span className="truncate">{triggerLabel}</span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open ? (
        <div
          role="listbox"
          className={cn(
            "absolute right-0 z-50 mt-1 min-w-[200px] rounded-md border border-border bg-card p-1 shadow-lg",
            menuClassName
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <FilterRow
            bold
            checked={allVisible}
            onChange={onToggleAll}
            label={allLabel}
            count={items.reduce((sum, i) => sum + (i.count ?? 0), 0)}
          />
          <div className="max-h-56 overflow-y-auto py-1">
            {items.map((item) => (
              <FilterRow
                key={item.id}
                checked={!hiddenIds.has(item.id)}
                onChange={() => onToggleItem(item.id)}
                label={item.label}
                count={item.count}
                icon={item.icon}
                description={item.description}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
