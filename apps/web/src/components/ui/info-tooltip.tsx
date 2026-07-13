import { CircleHelp } from "lucide-react";
import { cn } from "@/lib/utils";

export function InfoTooltip({
  text,
  className,
  side = "top",
}: {
  text: string;
  className?: string;
  side?: "top" | "bottom";
}) {
  return (
    <span
      className={cn("group relative inline-flex shrink-0 align-middle", className)}
    >
      <CircleHelp
        className="h-3.5 w-3.5 cursor-help text-muted-foreground"
        aria-label={text}
        tabIndex={0}
      />
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-1/2 z-50 hidden w-56 -translate-x-1/2 rounded-md border bg-card px-2.5 py-2 text-xs font-normal leading-snug text-card-foreground shadow-md",
          "group-hover:block group-focus-within:block",
          side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5"
        )}
      >
        {text}
      </span>
    </span>
  );
}

export function LabelWithTooltip({
  label,
  hint,
}: {
  label: string;
  hint: string;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <InfoTooltip text={hint} />
    </span>
  );
}
