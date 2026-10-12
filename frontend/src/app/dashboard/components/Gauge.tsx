"use client";

interface GaugeProps {
  value: number; // 0–100
  label?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
  /** Color threshold — uses accent when no threshold matches */
  thresholds?: Array<{ max: number; color: string }>;
}

const defaultThresholds = [
  { max: 25, color: "stroke-red-500" },
  { max: 50, color: "stroke-amber-500" },
  { max: 75, color: "stroke-yellow-500" },
  { max: 100, color: "stroke-emerald-500" },
];

const sizeConfig = {
  sm: { width: 40, height: 24, stroke: 3, fontSize: "text-[9px]" },
  md: { width: 64, height: 36, stroke: 4, fontSize: "text-xs" },
  lg: { width: 96, height: 52, stroke: 6, fontSize: "text-sm" },
};

/**
 * Gauge — circular semi-donut gauge for governance score, DQ score, confidence, etc.
 * Renders a clean SVG arc with colour thresholds.
 * Accepts explicit `size` for layout flexibility.
 */
export function Gauge({
  value,
  label,
  size = "md",
  className = "",
  thresholds = defaultThresholds,
}: GaugeProps) {
  const cfg = sizeConfig[size];
  const clamped = Math.max(0, Math.min(100, value));
  const r = (cfg.width - cfg.stroke) / 2;
  const circumference = Math.PI * r;
  const offset = circumference - (clamped / 100) * circumference;

  const color = thresholds.find((t) => clamped <= t.max)?.color ?? "stroke-accent";

  return (
    <div className={`inline-flex flex-col items-center gap-1 ${className}`}>
      <svg
        width={cfg.width}
        height={cfg.height}
        viewBox={`0 0 ${cfg.width} ${cfg.height}`}
        aria-label={label ? `${label}: ${clamped}%` : `${clamped}%`}
        role="meter"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {/* Background arc */}
        <path
          d={`M ${cfg.stroke / 2} ${cfg.height - cfg.stroke / 2} A ${r} ${r} 0 0 1 ${cfg.width - cfg.stroke / 2} ${cfg.height - cfg.stroke / 2}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={cfg.stroke}
          strokeLinecap="round"
          className="text-surface-overlay"
        />
        {/* Value arc */}
        <path
          d={`M ${cfg.stroke / 2} ${cfg.height - cfg.stroke / 2} A ${r} ${r} 0 0 1 ${cfg.width - cfg.stroke / 2} ${cfg.height - cfg.stroke / 2}`}
          fill="none"
          stroke="currentColor"
          strokeWidth={cfg.stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className={`transition-all duration-500 ${color}`}
        />
      </svg>
      <span className={`font-semibold tabular-nums ${cfg.fontSize} text-fg`}>
        {clamped}%
      </span>
      {label && (
        <span className="text-[10px] text-fg-subtle leading-tight text-center">
          {label}
        </span>
      )}
    </div>
  );
}