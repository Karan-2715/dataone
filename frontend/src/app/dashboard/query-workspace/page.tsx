"use client";
import { Suspense } from "react";
import QueryWorkspaceInner from "./QueryWorkspaceInner";

export default function QueryWorkspacePage() {
  return (
    <Suspense fallback={<div className="flex h-full items-center justify-center text-sm text-fg0">Loading workspace…</div>}>
      <QueryWorkspaceInner />
    </Suspense>
  );
}