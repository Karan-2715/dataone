"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

export type PanelState = "normal" | "collapsed" | "maximized";

export interface PanelLayout {
  state: PanelState;
  width: number;
}

interface WorkspaceLayoutDoc {
  panels: Record<string, PanelLayout>;
}

interface WorkspaceLayoutResponse {
  workspace_key: string;
  layout: Partial<WorkspaceLayoutDoc>;
}

const SAVE_DEBOUNCE_MS = 500;

/**
 * Persists a dockable workspace's panel states/widths per user (Enterprise
 * v2, E11-8). Falls back silently to the caller's defaults on a load
 * failure — a lost layout is a UX nicety regression, never a blocker — and
 * debounces saves so a resize drag doesn't fire a PUT per pixel.
 */
export function useWorkspaceLayout(workspaceKey: string, defaults: Record<string, PanelLayout>) {
  const [panels, setPanels] = useState<Record<string, PanelLayout>>(defaults);
  const [loaded, setLoaded] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const defaultsRef = useRef(defaults);
  defaultsRef.current = defaults;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<WorkspaceLayoutResponse>(`/api/v1/workspace-layout/${workspaceKey}`);
        if (!cancelled && res.layout?.panels) {
          setPanels({ ...defaultsRef.current, ...res.layout.panels });
        }
      } catch {
        // Layout persistence is a nicety; keep the caller's defaults.
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceKey]);

  const updatePanel = useCallback(
    (panelKey: string, patch: Partial<PanelLayout>) => {
      setPanels((prev) => {
        const next = {
          ...prev,
          [panelKey]: { ...(prev[panelKey] ?? defaultsRef.current[panelKey]), ...patch },
        };
        if (saveTimer.current) clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(() => {
          void api.put(`/api/v1/workspace-layout/${workspaceKey}`, { layout: { panels: next } });
        }, SAVE_DEBOUNCE_MS);
        return next;
      });
    },
    [workspaceKey],
  );

  return { panels, updatePanel, loaded };
}
