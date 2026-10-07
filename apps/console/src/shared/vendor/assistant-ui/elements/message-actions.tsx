"use client";

import type { ComponentProps, ReactNode } from "react";
import {
  CheckIcon,
  CopyIcon,
  EllipsisIcon,
  RefreshCwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
} from "lucide-react";
import { cn } from "../lib/utils";
import { ghostButton, iconSwap, iconSwapIn, iconSwapOut } from "./surfaces";

export type Reaction = "up" | "down" | null;

export interface MessageActionLabels {
  copy?: string;
  copied?: string;
  helpful?: string;
  unhelpful?: string;
  regenerate?: string;
  more?: string;
}

export interface MessageActionsProps extends Omit<
  ComponentProps<"div">,
  "children" | "onCopy"
> {
  copied?: boolean;
  reaction?: Reaction;
  regenerating?: boolean;
  reactionBusy?: boolean;
  allowClearReaction?: boolean;
  onCopy?: () => void;
  onReactionChange?: (reaction: Reaction) => void;
  onRegenerate?: () => void;
  onMore?: () => void;
  children?: ReactNode;
  labels?: MessageActionLabels;
}

export function MessageActions({
  copied = false,
  reaction = null,
  regenerating = false,
  reactionBusy = false,
  allowClearReaction = true,
  onCopy,
  onReactionChange,
  onRegenerate,
  onMore,
  children,
  labels,
  className,
  ...props
}: MessageActionsProps) {
  const buttonClassName = cn(ghostButton, "size-7");

  return (
    <div
      data-slot="message-actions"
      className={cn("flex items-center gap-1", className)}

      {...props}
    >
      {onCopy && <button
        type="button"
        aria-label={copied ? (labels?.copied ?? "Copied response") : (labels?.copy ?? "Copy response")}
        onClick={onCopy}
        className={cn(
          buttonClassName,
          "grid place-items-center",
          copied && "text-emerald-500",
        )}
      >
        <CopyIcon
          className={cn(
            iconSwap,
            "size-3.5",
            copied ? iconSwapOut : iconSwapIn,
          )}
        />
        <CheckIcon
          className={cn(
            iconSwap,
            "size-3.5",
            copied ? iconSwapIn : iconSwapOut,
          )}
        />
      </button>}
      {onReactionChange && <button
        type="button"
        aria-label={labels?.helpful ?? "Mark response helpful"}
        aria-pressed={reaction === "up"}
        aria-busy={reactionBusy}
        disabled={reactionBusy}
        aria-disabled={(!allowClearReaction && reaction === "up") || undefined}
        aria-description={(!allowClearReaction && reaction === "up") ? "This reaction is already selected and cannot be cleared." : undefined}
        title={(!allowClearReaction && reaction === "up") ? "This reaction is already selected and cannot be cleared." : undefined}
        onClick={reactionBusy || (!allowClearReaction && reaction === "up") ? undefined : () => onReactionChange(reaction === "up" && allowClearReaction ? null : "up")}
        className={cn(
          "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
          buttonClassName,
          reaction === "up" &&
            "bg-foreground/[0.06] text-foreground/90 dark:bg-foreground/[0.09]",
        )}
      >
        <ThumbsUpIcon className="size-3.5" />
      </button>}
      {onReactionChange && <button
        type="button"
        aria-label={labels?.unhelpful ?? "Mark response unhelpful"}
        aria-pressed={reaction === "down"}
        aria-busy={reactionBusy}
        disabled={reactionBusy}
        aria-disabled={(!allowClearReaction && reaction === "down") || undefined}
        aria-description={(!allowClearReaction && reaction === "down") ? "This reaction is already selected and cannot be cleared." : undefined}
        title={(!allowClearReaction && reaction === "down") ? "This reaction is already selected and cannot be cleared." : undefined}
        onClick={reactionBusy || (!allowClearReaction && reaction === "down") ? undefined : () => onReactionChange(reaction === "down" && allowClearReaction ? null : "down")}
        className={cn(
          "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
          buttonClassName,
          reaction === "down" &&
            "bg-foreground/[0.06] text-foreground/90 dark:bg-foreground/[0.09]",
        )}
      >
        <ThumbsDownIcon className="size-3.5" />
      </button>}
      {onRegenerate && <button
        type="button"
        aria-label={labels?.regenerate ?? "Regenerate response"}
        onClick={onRegenerate}
        disabled={regenerating}
        aria-busy={regenerating}
        className={buttonClassName}
      >
        <RefreshCwIcon
          className={cn(
            "size-3.5",
            regenerating && "animate-spin motion-reduce:animate-none",
          )}
        />
      </button>}
      {onMore && <button
        type="button"
        aria-label={labels?.more ?? "More response actions"}
        onClick={onMore}
        className={buttonClassName}
      >
        <EllipsisIcon className="size-3.5" />
      </button>}
      {children}
    </div>
  );
}
