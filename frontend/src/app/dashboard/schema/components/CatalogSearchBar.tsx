"use client";

interface CatalogSearchBarProps {
  q: string;
  onQChange: (v: string) => void;
  dataType: string;
  onDataTypeChange: (v: string) => void;
  classificationLabel: string;
  onClassificationLabelChange: (v: string) => void;
}

export default function CatalogSearchBar({
  q, onQChange, dataType, onDataTypeChange, classificationLabel, onClassificationLabelChange,
}: CatalogSearchBarProps) {
  return (
    <div className="glass flex flex-wrap items-center gap-2 rounded-xl p-3" role="search" aria-label="Filter catalog">
      <input
        type="text"
        value={q}
        onChange={(e) => onQChange(e.target.value)}
        placeholder="Search table or column name…"
        aria-label="Search table or column"
        className="min-w-[200px] flex-1 rounded-lg border border-border-strong bg-surface-overlay px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none"
      />
      <input
        type="text"
        value={dataType}
        onChange={(e) => onDataTypeChange(e.target.value)}
        placeholder="Data type (e.g. TEXT)"
        aria-label="Filter by data type"
        className="w-40 rounded-lg border border-border-strong bg-surface-overlay px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none"
      />
      <select
        value={classificationLabel}
        onChange={(e) => onClassificationLabelChange(e.target.value)}
        aria-label="Filter by classification"
        className="rounded-lg border border-border-strong bg-surface-overlay px-3 py-2 text-sm text-fg focus:border-accent focus:outline-none"
      >
        <option value="">All classifications</option>
        <option value="PII">PII</option>
        <option value="Sensitive">Sensitive</option>
        <option value="Public">Public</option>
      </select>
    </div>
  );
}
