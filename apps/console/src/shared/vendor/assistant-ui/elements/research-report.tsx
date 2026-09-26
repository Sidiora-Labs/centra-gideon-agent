"use client";

import type { ComponentProps } from "react";
import { CheckIcon, Loader2Icon } from "lucide-react";
import { cn } from "../lib/utils";
import { mono, paper } from "./surfaces";

export type SectionState = "pending" | "writing" | "done";

export interface ReportSection {
  id: string;
  heading: string;
  state: SectionState;
  sources: number;
  preview?: string;
}

export function ResearchReport({
  title,
  prompt,
  details,
  lastStatus,
  statusError = false,
  lastRun,
  lastError,
  sections,
  sourcesRead,
  compact = false,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "title" | "sections" | "sourcesRead"
> & {
  title: string;
  prompt?: string;
  details?: readonly string[];
  lastStatus?: string;
  statusError?: boolean;
  lastRun?: string;
  lastError?: string;
  sections?: readonly ReportSection[];
  sourcesRead?: number;
  compact?: boolean;
}) {
  const progress = sections ? `${sections.filter((section) => section.state === "done").length}/${sections.length} sections` : null;
  const sourceCount = Number.isFinite(sourcesRead) ? `${sourcesRead} sources read` : null;
  const metrics = [progress, sourceCount].filter(Boolean).join(" · ");

  return (
    <div
      data-slot="research-report"
      className={cn(
        !compact && paper,
        compact ? "flex min-w-0 flex-1 flex-col gap-1" : "flex w-full max-w-sm flex-col gap-3 rounded-2xl p-4",
        className,
      )}

      {...props}
    >
      <div className="flex flex-col gap-1">
        <span className="text-[13.5px] font-medium">{title}</span>
        {prompt && <p className="text-foreground/65 text-xs leading-relaxed">{prompt}</p>}
        {details && details.length > 0 && <p className="text-foreground/45 text-xs">{details.join(" · ")}</p>}
        {(lastStatus || lastRun) && <div className={cn(mono, "text-foreground/40 flex flex-wrap gap-x-2")}>
          {lastStatus && <span role={statusError ? "alert" : undefined}>
            {lastStatus}{lastError && ` · ${lastError}`}
          </span>}
          {lastRun && <span>{lastRun}</span>}
        </div>}
        {metrics && <span className={cn(mono, "text-foreground/30 tabular-nums")}>{metrics}</span>}
      </div>

      {sections && <div className="flex flex-col">
        {sections.map((section) => (
          <div
            key={section.id}
            className="border-foreground/[0.06] flex flex-col gap-1 border-t py-2 first:border-t-0 first:pt-0"
          >
            <div className="flex items-center gap-2">
              <span className="flex size-3.5 shrink-0 items-center justify-center">
                {section.state === "done" ? (
                  <CheckIcon className="text-foreground/35 size-3" />
                ) : section.state === "writing" ? (
                  <Loader2Icon className="size-3 animate-spin text-blue-500 motion-reduce:animate-none dark:text-blue-400" />
                ) : (
                  <span
                    aria-hidden
                    className="bg-foreground/15 size-1.5 rounded-full"
                  />
                )}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-[13px]",
                  section.state === "pending"
                    ? "text-foreground/35"
                    : "text-foreground/85",
                )}
              >
                {section.heading}
              </span>
              {section.sources > 0 && (
                <span className={cn(mono, "text-foreground/25 shrink-0")}>
                  {section.sources} src
                </span>
              )}
            </div>
            {section.preview && (
              <p className="text-foreground/50 fade-in animate-in ps-5.5 text-xs leading-relaxed duration-300">
                {section.preview}
              </p>
            )}
          </div>
        ))}
      </div>}
    </div>
  );
}
