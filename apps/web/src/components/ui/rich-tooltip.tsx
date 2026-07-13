import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function RichTooltip({
  children,
  content,
  side = "top",
  className,
}: {
  children: ReactNode;
  content: ReactNode;
  side?: "top" | "bottom";
  className?: string;
}) {
  return (
    <span
      className={cn(
        "group/rich-tip relative inline-flex shrink-0 align-middle",
        className
      )}
    >
      {children}
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none absolute left-1/2 z-50 hidden w-max max-w-[320px] -translate-x-1/2 rounded-md border border-border bg-card px-3 py-2 text-left text-xs font-normal leading-snug text-card-foreground shadow-lg",
          "group-hover/rich-tip:block group-focus-within/rich-tip:block",
          side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5"
        )}
      >
        {content}
      </span>
    </span>
  );
}
