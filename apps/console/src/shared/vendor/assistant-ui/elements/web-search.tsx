"use client";

import type { ComponentProps } from "react";
import { SearchIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { field, mono, ShimmerLabel } from "./surfaces";
import { take } from "../utils/range";

export interface WebSearchResult {
  id?: string;
  title: string;
  domain: string;
  summary?: string;
  url?: string;
}

function externalUrl(value?: string): string | null {
  if (!value || !/^https?:\/\//i.test(value)) return null;
  try {
    return new URL(value).href;
  } catch {
    return null;
  }
}

export function WebSearch({
  query,
  results,
  visibleResults,
  searching,
  cycle,
  onSelect,
  onOpen,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "query" | "results" | "visibleResults" | "searching" | "cycle" | "onSelect" | "onOpen"
> & {
  query: string;
  results: readonly WebSearchResult[];
  visibleResults: number;
  searching: boolean;
  cycle: number;
  onSelect?: (result: WebSearchResult) => void;
  onOpen?: (result: WebSearchResult) => void;
}) {
  return (
    <div
      data-slot="web-search"
      className={cn("flex w-full max-w-sm flex-col gap-2.5", className)}

      {...props}
    >
      <span
        className={cn(
          field,
          "text-foreground/70 inline-flex w-fit items-center gap-1.5 rounded-full px-3.5 py-2 text-xs",
        )}
      >
        <SearchIcon className="text-foreground/40 size-3" />
        {query}
      </span>
      <div className="text-foreground/45 text-xs">
        {searching ? (
          <ShimmerLabel className="relative inline-block leading-none">
            Searching
          </ShimmerLabel>
        ) : (
          <span className="fade-in animate-in duration-300">
            {results.length} results
          </span>
        )}
      </div>
      <div className="flex min-h-[5.75rem] flex-col">
        {take(results, visibleResults).map((result, index) => {
          const url = externalUrl(result.url);
          const content = <>
            <span className="bg-foreground/[0.06] text-foreground/45 flex size-4 shrink-0 items-center justify-center rounded text-[9px] font-medium">
              {result.domain.charAt(0).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="text-foreground/90 block truncate text-[13.5px]">{result.title}</span>
              {result.summary && <span className="text-foreground/45 block line-clamp-2 text-xs">{result.summary}</span>}
            </span>
            <span className={cn(mono, "text-foreground/35 shrink-0")}>{result.domain}</span>
          </>;
          return (
            <div
              key={`${cycle}-${result.id ?? `${result.domain}-${index}`}`}
              className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both hover:bg-foreground/[0.03] -mx-2.5 flex items-center gap-2.5 rounded-xl px-2.5 py-1.5 transition-colors duration-300"
            >
              {onSelect ? (
                <button type="button" onClick={() => onSelect(result)} aria-label={`Select ${result.title}`}
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 text-start focus-visible:outline-2 focus-visible:outline-offset-2">
                  {content}
                </button>
              ) : <span className="flex min-w-0 flex-1 items-center gap-2.5">{content}</span>}
              {url && <a href={url} target="_blank" rel="noopener noreferrer"
                onClick={onOpen ? () => onOpen(result) : undefined} aria-label={`Open ${result.title} in new tab`}
                className="text-foreground/70 shrink-0 text-xs underline focus-visible:outline-2 focus-visible:outline-offset-2">
                Open
              </a>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
