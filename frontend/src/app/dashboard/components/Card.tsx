"use client";

import { type ReactNode } from "react";

export type CardVariant = "default" | "glass" | "glass-strong" | "elevated";
export type CardPadding = "sm" | "md" | "lg";

interface CardProps {
  children: ReactNode;
  variant?: CardVariant;
  padding?: CardPadding;
  className?: string;
  as?: "div" | "section" | "article";
  onClick?: () => void;
  hoverable?: boolean;
  /** ARIA label for interactive cards */
  "aria-label"?: string;
}

const paddingMap: Record<CardPadding, string> = {
  sm: "p-4",
  md: "p-5",
  lg: "p-6",
};

const variantMap: Record<CardVariant, string> = {
  default: "bg-surface-elevated border border-border",
  glass: "glass",
  "glass-strong": "glass-strong",
  elevated: "bg-surface-elevated border border-border shadow-md",
};

/**
 * Shared Card primitive — the base surface for all dashboard widgets, panels, and tiles.
 * Renders inside the E01 design system with glassmorphism support.
 *
 * - `glass` variant: translucent with backdrop-blur (default for panels)
 * - `default` variant: solid surface for data-dense contexts
 * - `hoverable` adds interactive hover elevation
 */
export function Card({
  children,
  variant = "default",
  padding = "md",
  className = "",
  as: Component = "div",
  onClick,
  hoverable = false,
  "aria-label": ariaLabel,
}: CardProps) {
  return (
    <Component
      className={[
        "rounded-2xl transition-all duration-200",
        paddingMap[padding],
        variantMap[variant],
        hoverable && !onClick ? "hover:border-border-strong hover:shadow-md cursor-default" : "",
        onClick ? "cursor-pointer hover:border-border-strong hover:shadow-md" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e: React.KeyboardEvent) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      aria-label={ariaLabel}
    >
      {children}
    </Component>
  );
}