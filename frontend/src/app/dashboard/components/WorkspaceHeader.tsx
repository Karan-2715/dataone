"use client";

import type { ReactNode } from "react";

interface WorkspaceHeaderProps {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
  className?: string;
}

export function WorkspaceHeader({ eyebrow, title, description, actions, className = "" }: WorkspaceHeaderProps) {
  return (
    <header className={`workspace-header ${className}`}>
      <div className="min-w-0">
        {eyebrow && <p className="text-fg-subtle text-[11px] font-semibold tracking-widest uppercase mb-1">{eyebrow}</p>}
        <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-fg-muted">{description}</p>
      </div>
      {actions && <div className="workspace-header-actions flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

interface Segment<T extends string> { value: T; label: string; indicator?: boolean }
interface SegmentedControlProps<T extends string> {
  label: string;
  value: T;
  options: Segment<T>[];
  onChange: (value: T) => void;
}

export function SegmentedControl<T extends string>({ label, value, options, onChange }: SegmentedControlProps<T>) {
  return (
    <div className="workspace-segments" role="group" aria-label={label}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button key={option.value} type="button" aria-pressed={selected} onClick={() => onChange(option.value)} className={`relative min-w-[4rem] rounded-lg px-3 py-1.5 text-xs font-semibold transition-all ${selected ? 'bg-surface-elevated text-fg shadow-sm border border-border' : 'text-fg-muted hover:text-fg'}`}>
            {option.label}
            {option.indicator && !selected && <span className="absolute right-1 top-1 w-1.5 h-1.5 rounded-full bg-fg" aria-label="Completed in background" />}
          </button>
        );
      })}
    </div>
  );
}
