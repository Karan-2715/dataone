"use client";
import { useState } from "react";
import type { VizView } from "../lib/types";

interface SaveViewDialogProps {
  savedViews: VizView[];
  onSave: (name: string) => Promise<void>;
  onLoad: (view: VizView) => void;
  onDelete: (viewId: number) => void;
  canSave: boolean;
}

export default function SaveViewDialog({ savedViews, onSave, onLoad, onDelete, canSave }: SaveViewDialogProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await onSave(name.trim());
      setName("");
    } catch {
      // toast already shown by hook
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="px-3 py-2 text-xs font-semibold text-fg-muted border border-border-strong rounded-lg hover:bg-surface-overlay"
      >
        Views {savedViews.length > 0 ? `(${savedViews.length})` : ""}
      </button>
      {open && (
        <div className="absolute right-0 mt-2 w-72 rounded-xl bg-surface border border-border shadow-2xl z-40 p-3">
          {canSave && (
            <div className="flex items-center gap-2 mb-3">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Save current view as…"
                className="flex-1 px-2 py-1.5 text-xs rounded-lg bg-surface-overlay border border-border-strong text-fg-muted"
              />
              <button
                type="button"
                onClick={() => void save()}
                disabled={saving || !name.trim()}
                className="px-2.5 py-1.5 text-xs font-semibold bg-accent text-white rounded-lg hover:opacity-90 disabled:opacity-50"
              >
                Save
              </button>
            </div>
          )}
          <div className="flex flex-col gap-1 max-h-64 overflow-y-auto">
            {savedViews.length === 0 ? (
              <p className="text-[11px] text-fg-subtle text-center py-3">No saved views yet.</p>
            ) : (
              savedViews.map((v) => (
                <div key={v.id} className="flex items-center justify-between px-2 py-1.5 rounded-lg hover:bg-surface-overlay text-xs">
                  <button type="button" onClick={() => { onLoad(v); setOpen(false); }} className="flex-1 text-left text-fg-muted">
                    {v.name} <span className="text-fg-subtle">· {v.chart_type}</span>
                  </button>
                  {canSave && (
                    <button type="button" onClick={() => onDelete(v.id)} aria-label="Delete view" className="text-fg-subtle hover:text-danger px-1">
                      ✕
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
