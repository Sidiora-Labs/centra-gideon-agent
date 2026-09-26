"use client";

import type { ComponentProps, ReactNode } from "react";
import { ArrowUpRightIcon, FileTextIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { mono, paper, ShimmerLabel } from "./surfaces";

export function ArtifactCard({
  title,
  meta,
  generating = false,
  words = 0,
  preview,
  icon,
  details,
  embedded = false,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "title" | "meta" | "generating" | "words"
> & {
  title: string;
  meta: string;
  generating?: boolean;
  words?: number;
  preview?: ReactNode;
  icon?: ReactNode;
  details?: ReactNode;
  embedded?: boolean;
}) {
  return (
    <div
      data-slot="artifact-card"
      className={cn(
        embedded
          ? "flex w-full min-w-0 flex-col text-left"
          : cn(paper, "group flex w-full max-w-xs cursor-pointer items-center gap-3 rounded-[20px] p-3.5 transition-transform duration-150 hover:-translate-y-px active:scale-[0.98]"),
        className,
      )}

      {...props}
    >
      {preview && <div data-slot="artifact-preview" className="h-36 w-full shrink-0 overflow-hidden border-b border-outline-variant/30 bg-surface">{preview}</div>}
      <div className={embedded ? "flex w-full min-w-0 items-center gap-3 px-3 py-2" : "contents"}>
      <span className="bg-foreground/[0.05] text-foreground/45 flex size-9 shrink-0 items-center justify-center rounded-xl">
        {icon ?? <FileTextIcon
          className={cn(
            "size-4",
            generating && "animate-pulse motion-reduce:animate-none",
          )}
        />}
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("text-[13.5px] font-medium", embedded ? "line-clamp-2 break-words [overflow-wrap:anywhere]" : "truncate")}>{title}</p>
        {generating ? (
          <p className={cn(mono, "text-foreground/40 flex items-center gap-1")}>
            <ShimmerLabel className="relative inline-block leading-none">
              Writing
            </ShimmerLabel>
            <span>·</span>
            <span className="tabular-nums">{words} words</span>
          </p>
        ) : (
          <p
            className={cn(
              mono,
              "fade-in blur-in-[2px] animate-in text-foreground/40 duration-300 motion-reduce:animate-none",
              embedded && "break-words [overflow-wrap:anywhere]",
            )}
          >
            {meta}
          </p>
        )}
        {details}
      </div>
      <ArrowUpRightIcon className="text-foreground/35 size-3.5 opacity-0 transition-opacity group-hover:opacity-100" />
      </div>
    </div>
  );
}
