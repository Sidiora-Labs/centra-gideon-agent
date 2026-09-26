"use client";

import type { ComponentProps } from "react";
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { ghostButton, mono } from "./surfaces";

export interface MessageBranchLabels {
  previous?: string;
  next?: string;
}

export interface MessageBranchesProps extends Omit<
  ComponentProps<"div">,
  "children"
> {
  variants?: readonly string[];
  count?: number;
  showBody?: boolean;
  labels?: MessageBranchLabels;
  index: number;
  onIndexChange?: (index: number) => void;
}

export function MessageBranches({
  variants,
  count,
  showBody = true,
  labels,
  index,
  onIndexChange,
  className,
  ...props
}: MessageBranchesProps) {
  const total = count ?? variants?.length ?? 0;
  const message = variants?.[index] ?? variants?.[0] ?? "";
  const hasNavigation = total > 1 && !!onIndexChange;

  const goPrevious = () => {
    onIndexChange!(index === 0 ? total - 1 : index - 1);
  };
  const goNext = () => {
    onIndexChange!(index === total - 1 ? 0 : index + 1);
  };

  return (
    <div
      data-slot="message-branches"
      className={cn("flex max-w-sm flex-col gap-2", className)}

      {...props}
    >
      {showBody && variants && <p
        key={index}
        className="fade-in slide-in-from-bottom-1 animate-in text-foreground/90 min-h-[4.25rem] text-sm leading-relaxed duration-300 motion-reduce:animate-none"
      >
        {message}
      </p>}
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label={labels?.previous ?? "Show previous response"}
          disabled={!hasNavigation}
          onClick={goPrevious}
          className={cn(ghostButton, "size-6")}
        >
          <ChevronLeftIcon className="size-3.5" />
        </button>
        <span className={cn(mono, "text-foreground/35 tabular-nums")}>
          {total === 0
            ? "0 / 0"
            : `${index + 1} / ${total}`}
        </span>
        <button
          type="button"
          aria-label={labels?.next ?? "Show next response"}
          disabled={!hasNavigation}
          onClick={goNext}
          className={cn(ghostButton, "size-6")}
        >
          <ChevronRightIcon className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
