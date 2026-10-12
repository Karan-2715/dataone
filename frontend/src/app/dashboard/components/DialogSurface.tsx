"use client";

import { useEffect, useId, type ReactNode } from "react";

interface DialogSurfaceProps {
  title: string;
  description?: string;
  eyebrow?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: "sm" | "md" | "lg";
}

const widthClass = { sm: "max-w-md", md: "max-w-2xl", lg: "max-w-4xl" };

export function DialogSurface({ title, description, eyebrow, onClose, children, footer, width = "sm" }: DialogSurfaceProps) {
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className={`glass-strong flex max-h-[85vh] w-full ${widthClass[width]} flex-col overflow-hidden rounded-2xl`} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}>
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            {eyebrow && <p className="workspace-eyebrow">{eyebrow}</p>}
            <h2 id={titleId} className="text-base font-semibold text-fg">{title}</h2>
            {description && <p id={descriptionId} className="mt-1 text-xs text-fg-subtle">{description}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label={`Close ${title}`} className="shrink-0 rounded-lg px-2 py-1 text-xs font-semibold text-fg-subtle hover:bg-surface-overlay hover:text-fg">✕</button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-4">{footer}</footer>}
      </section>
    </div>
  );
}
