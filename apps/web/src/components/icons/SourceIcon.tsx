import { getSourceDefinition, sourceIconUrl } from "@/lib/sourceCatalog";
import { cn } from "@/lib/utils";

export function SourceIcon({
  source,
  className,
  muted = false,
}: {
  source: string | null | undefined;
  className?: string;
  muted?: boolean;
}) {
  const def = getSourceDefinition(source);
  const src = sourceIconUrl(source);

  if (src) {
    const shared = cn(
      "h-5 w-5 shrink-0 rounded-sm",
      muted && "opacity-40 grayscale",
      className
    );

    // Simple Icons SVGs are monochrome (default black). Tint via CSS mask.
    if (!def.coloredIcon) {
      return (
        <span
          className={shared}
          style={{
            backgroundColor: muted ? "#94A3B8" : `#${def.brandColor}`,
            WebkitMaskImage: `url(${src})`,
            WebkitMaskRepeat: "no-repeat",
            WebkitMaskPosition: "center",
            WebkitMaskSize: "contain",
            maskImage: `url(${src})`,
            maskRepeat: "no-repeat",
            maskPosition: "center",
            maskSize: "contain",
          }}
          role="img"
          aria-label={def.label}
        />
      );
    }

    return (
      <img
        src={src}
        alt=""
        className={cn(shared, "object-contain")}
        draggable={false}
      />
    );
  }

  return (
    <span
      className={cn(
        "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[9px] font-bold uppercase text-white",
        muted && "opacity-40 grayscale",
        className
      )}
      style={{ backgroundColor: `#${def.brandColor}` }}
      aria-hidden
    >
      {def.label.slice(0, 2)}
    </span>
  );
}
