"use client";

import type { ComponentProps } from "react";
import { cn } from "../lib/utils";
import { field, mono } from "./surfaces";
import { announced, pct } from "../utils/range";

const fmt = (n: number) => n.toLocaleString("en-US");

export interface EffortLevel {
  key: string;
  label: string;
  budget?: number | null;
}

export function ReasoningEffort({
  levels,
  selectedKey,
  heading = "Thinking",
  spent,
  onSelect,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "levels" | "selectedKey" | "heading" | "spent" | "onSelect"
> & {
  levels: readonly EffortLevel[];
  selectedKey: string;
  heading?: string;
  spent?: number | null;
  onSelect?: (key: string) => void;
}) {
  const selected = levels.find((level) => level.key === selectedKey);
  const budget = selected?.budget;
  const usage =
    typeof spent === "number" && Number.isFinite(spent) &&
    typeof budget === "number" && Number.isFinite(budget)
      ? { spent, budget, used: pct(spent, budget) }
      : undefined;

  return (
    <div
      data-slot="reasoning-effort"
      className={cn("flex w-full max-w-sm flex-col gap-2.5", className)}

      {...props}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[13.5px] font-medium">{heading}</span>
        {usage && <span className={cn(mono, "text-foreground/35 tabular-nums")}>
          {fmt(usage.spent)} / {fmt(usage.budget)}
        </span>}
      </div>

      <div className={cn(field, "flex gap-0.5 rounded-full p-0.5")}>
        {levels.map((level) => {
          const active = level.key === selectedKey;
          const className = cn(
            "flex-1 rounded-full py-1 text-xs font-medium transition-[background-color,color,scale] duration-150",
            onSelect && "active:scale-[0.97]",
            active
              ? "bg-background text-foreground/90"
              : onSelect
                ? "text-foreground/45 hover:text-foreground/70"
                : "text-foreground/45",
          );
          return onSelect ? (
            <button
              key={level.key}
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(level.key)}
              className={className}
            >
              {level.label}
            </button>
          ) : (
            <span
              key={level.key}
              aria-current={active ? "true" : undefined}
              className={className}
            >
              {level.label}
            </span>
          );
        })}
      </div>

      {usage && <span
        role="progressbar"
        aria-label="Thinking budget used"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={announced(usage.used)}
        aria-valuetext={`${fmt(usage.spent)} of ${fmt(usage.budget)}`}
        className="bg-foreground/[0.06] h-[3px] w-full overflow-hidden rounded-full"
      >
        <span
          className="block h-full rounded-full bg-blue-500 transition-[width] duration-500 motion-reduce:transition-none dark:bg-blue-400"
          style={{ width: `${usage.used}%` }}
        />
      </span>}
    </div>
  );
}
