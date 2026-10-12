import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface LLMModel {
  id: string;
  name: string;
  description: string;
}

export function LLMSelectModal({ 
  onComplete 
}: { 
  onComplete: (modelId: string) => void 
}) {
  const [models, setModels] = useState<LLMModel[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.get<LLMModel[]>("/api/v1/llm/models")
      .then(res => {
        setModels(res);
        if (res.length > 0) setSelected(res[0].id);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await api.put("/api/v1/auth/me/llm_model", { llm_model: selected });
      onComplete(selected);
    } catch (err) {
      console.error(err);
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg overflow-hidden rounded-2xl bg-[#0e0e11] border border-white/10 shadow-2xl">
        <div className="p-6">
          <h2 className="text-xl font-semibold text-white">Select AI Model for Schema Mapping</h2>
          <p className="mt-3 text-sm text-white/60 leading-relaxed">
            DataOne requires an LLM to accurately parse complex schema mapping instructions, understand semantic similarity across columns, and generate valid transformation rules. 
            Please select an approved Databricks model to proceed.
          </p>

          <div className="mt-6 space-y-3">
            {loading ? (
              <div className="text-white/40 text-sm">Loading models...</div>
            ) : (
              models.map((m) => (
                <label 
                  key={m.id} 
                  className={`flex cursor-pointer flex-col rounded-xl border p-4 transition-colors ${
                    selected === m.id 
                      ? "border-blue-500/50 bg-blue-500/10" 
                      : "border-white/10 bg-white/5 hover:bg-white/10"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <input 
                      type="radio" 
                      name="llm_model" 
                      value={m.id}
                      checked={selected === m.id}
                      onChange={() => setSelected(m.id)}
                      className="h-4 w-4 accent-blue-500"
                    />
                    <span className="font-medium text-white">{m.name}</span>
                  </div>
                  <p className="mt-1 pl-7 text-xs text-white/50">{m.description}</p>
                </label>
              ))
            )}
          </div>
        </div>
        <div className="flex items-center justify-end border-t border-white/10 bg-[#161619] p-4">
          <button 
            onClick={handleSave}
            disabled={saving || !selected}
            className="rounded-lg bg-white px-5 py-2 text-sm font-semibold text-black hover:bg-white/90 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save Selection"}
          </button>
        </div>
      </div>
    </div>
  );
}
