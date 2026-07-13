import { SourceIcon } from "@/components/icons/SourceIcon";
import { SourcePreviewContent } from "@/components/SourcePreviewContent";
import { RichTooltip } from "@/components/ui/rich-tooltip";
import { getSourceDefinition } from "@/lib/sourceCatalog";
import type { ImportSource, Transaction } from "@/lib/types";

export function SourceBadge({
  source,
  importId,
  importSources = [],
  transactions = [],
}: {
  source: string | null | undefined;
  importId?: string | null;
  importSources?: ImportSource[];
  transactions?: Transaction[];
}) {
  const def = getSourceDefinition(source);
  return (
    <RichTooltip
      content={
        <SourcePreviewContent
          source={source}
          importSources={importSources}
          importId={importId}
          transactions={transactions}
        />
      }
    >
      <span
        className="inline-flex items-center gap-1.5"
        aria-label={def.label}
        tabIndex={0}
      >
        <SourceIcon source={source} className="h-4 w-4" />
        <span>{def.label}</span>
      </span>
    </RichTooltip>
  );
}
