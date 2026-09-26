"use client";

import type { ComponentProps } from "react";
import { DatabaseIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { field, mono, paper, ShimmerLabel } from "./surfaces";
import { announced, pct, take } from "../utils/range";

export interface RetrievalChunk {
  id: string;
  source: string;
  locator: string;
  score?: number | null;
  text: string;
  tokens?: number;
  sourceType?: string | null;
  section?: string | null;
  lineRange?: readonly [number, number] | null;
  deepLink?: string | null;
}

export function RetrievalChunks({
  query,
  chunks,
  visibleCount,
  searching,
  onSelect,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "query" | "chunks" | "visibleCount" | "searching" | "onSelect"
> & {
  query: string;
  chunks: readonly RetrievalChunk[];
  visibleCount: number;
  searching: boolean;
  onSelect?: (chunk: RetrievalChunk) => void;
}) {
  return (
    <div
      data-slot="retrieval-chunks"
      className={cn("flex w-full max-w-sm flex-col gap-2.5", className)}

      {...props}
    >
      <span
        className={cn(
          field,
          "text-foreground/70 inline-flex w-fit items-center gap-1.5 rounded-full px-3.5 py-2 text-xs",
        )}
      >
        <DatabaseIcon className="text-foreground/40 size-3" />
        {query}
      </span>

      <div className="text-foreground/45 text-xs">
        {searching ? (
          <ShimmerLabel className="relative inline-block leading-none">
            Retrieving
          </ShimmerLabel>
        ) : (
          <span className="fade-in animate-in duration-300">
            {chunks.length} passages
          </span>
        )}
      </div>

      <div className="flex min-h-[7rem] flex-col gap-1.5">
        {take(chunks, visibleCount).map((chunk) => {
          const score = typeof chunk.score === "number" && Number.isFinite(chunk.score)
            && chunk.score >= 0 && chunk.score <= 1 ? chunk.score : null;
          const tokens = typeof chunk.tokens === "number" && Number.isFinite(chunk.tokens)
            && chunk.tokens >= 0 ? chunk.tokens : null;
          const cardClass = cn(
            paper,
            "fade-in slide-in-from-bottom-1 animate-in fill-mode-both flex w-full flex-col gap-1.5 rounded-2xl px-3.5 py-2.5 text-start duration-300",
            onSelect && "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500",
          );
          const content = <>
            <div className="flex items-baseline gap-2">
              <span className="text-foreground/90 min-w-0 flex-1 truncate text-[13px] font-medium">
                {chunk.source}
              </span>
              <span className={cn(mono, "text-foreground/30 shrink-0")}>
                {chunk.locator}
              </span>
              {score !== null && <span
                className={cn(
                  mono,
                  "shrink-0 tabular-nums",
                  score >= 0.8
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-foreground/35",
                )}
              >
                {score.toFixed(2)}
              </span>}
            </div>
            <p className="text-foreground/55 line-clamp-2 text-xs leading-relaxed">
              {chunk.text}
            </p>
            {(chunk.sourceType || chunk.section || chunk.lineRange || tokens !== null) &&
              <span className={cn(mono, "text-foreground/35 flex flex-wrap gap-x-2 text-xs")}>
                {chunk.sourceType && <span>{chunk.sourceType}</span>}
                {chunk.section && <span>{chunk.section}</span>}
                {chunk.lineRange && <span>Lines {chunk.lineRange[0]}–{chunk.lineRange[1]}</span>}
                {tokens !== null && <span>{tokens} tokens</span>}
              </span>}
            {score !== null && <span
              role="meter"
              aria-label={`${chunk.source} relevance score`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={announced(pct(score, 1))}
              aria-valuetext={`${score.toFixed(2)} of 1.00`}
              className="bg-foreground/[0.06] h-[2px] w-full overflow-hidden rounded-full"
            >
              <span
                className="block h-full rounded-full bg-blue-500/70 transition-[width] duration-500 dark:bg-blue-400/70"
                style={{ width: `${pct(score, 1)}%` }}
              />
            </span>}
          </>;
          return onSelect ? <button key={chunk.id} type="button" className={cardClass}
            aria-label={chunk.locator ? `Open source ${chunk.source}, ${chunk.locator}` : `Open source ${chunk.source}`}
            onClick={() => onSelect(chunk)}>{content}</button>
            : <div key={chunk.id} className={cardClass}>{content}</div>;
        })}
      </div>
    </div>
  );
}
