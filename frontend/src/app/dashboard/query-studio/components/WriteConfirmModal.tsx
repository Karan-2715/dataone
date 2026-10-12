"use client";

export default function WriteConfirmModal({
  statementType,
  warnings,
  onConfirm,
  onCancel,
}: {
  statementType: string;
  warnings: string[];
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" role="dialog" aria-modal="true">
      <div className="bg-surface border border-border-strong rounded-2xl p-6 max-w-md w-full flex flex-col gap-4">
        <div className="text-lg font-semibold text-amber-400">
          Confirm {statementType.toUpperCase()} statement
        </div>
        <div className="text-sm text-fg-subtle">
          This statement will modify the connected database. This cannot be undone automatically.
        </div>
        {warnings.length > 0 && (
          <ul className="text-xs text-amber-300 list-disc list-inside">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        )}
        <div className="flex justify-end gap-2 mt-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm bg-surface-overlay hover:bg-surface-overlay text-fg-muted rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 text-white rounded-lg"
          >
            Run anyway
          </button>
        </div>
      </div>
    </div>
  );
}
