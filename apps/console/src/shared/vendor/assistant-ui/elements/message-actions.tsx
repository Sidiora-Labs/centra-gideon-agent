"use client";


import { Button } from "../../../ui/Button";
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
      {onCopy && <Button type="button" ariaLabel={copied ? (labels?.copied ?? "Copied response") : (labels?.copy ?? "Copy response")} onClick={onCopy} className={cn(
          buttonClassName,
          "grid place-items-center",
          copied && "text-emerald-500",
        )} variant="ghost" size="xs" style={{ width: '1.75rem', height: '1.75rem', padding: 0 }}>
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
      </Button>}
      {onReactionChange && <Button type="button" ariaLabel={labels?.helpful ?? "Mark response helpful"} ariaPressed={reaction === "up"} loading={reactionBusy} disabledReason={(!allowClearReaction && reaction === "up") ? "This reaction is already selected and cannot be cleared." : undefined} title={(!allowClearReaction && reaction === "up") ? "This reaction is already selected and cannot be cleared." : undefined} onClick={reactionBusy || (!allowClearReaction && reaction === "up") ? undefined : () => onReactionChange(reaction === "up" && allowClearReaction ? null : "up")} className={cn(
          "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
          buttonClassName,
          reaction === "up" &&
            "bg-foreground/[0.06] text-foreground/90 dark:bg-foreground/[0.09]",
        )} disabled={(reactionBusy) || ((!allowClearReaction && reaction === "up") || undefined)} variant="ghost" size="xs" style={{ width: '1.75rem', height: '1.75rem', padding: 0 }}>
        <ThumbsUpIcon className="size-3.5" />
      </Button>}
      {onReactionChange && <Button type="button" ariaLabel={labels?.unhelpful ?? "Mark response unhelpful"} ariaPressed={reaction === "down"} loading={reactionBusy} disabledReason={(!allowClearReaction && reaction === "down") ? "This reaction is already selected and cannot be cleared." : undefined} title={(!allowClearReaction && reaction === "down") ? "This reaction is already selected and cannot be cleared." : undefined} onClick={reactionBusy || (!allowClearReaction && reaction === "down") ? undefined : () => onReactionChange(reaction === "down" && allowClearReaction ? null : "down")} className={cn(
          "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
          buttonClassName,
          reaction === "down" &&
            "bg-foreground/[0.06] text-foreground/90 dark:bg-foreground/[0.09]",
        )} disabled={(reactionBusy) || ((!allowClearReaction && reaction === "down") || undefined)} variant="ghost" size="xs" style={{ width: '1.75rem', height: '1.75rem', padding: 0 }}>
        <ThumbsDownIcon className="size-3.5" />
      </Button>}
      {onRegenerate && <Button type="button" ariaLabel={labels?.regenerate ?? "Regenerate response"} onClick={onRegenerate} loading={regenerating} className={buttonClassName} disabled={regenerating} variant="ghost" size="xs" style={{ width: '1.75rem', height: '1.75rem', padding: 0 }}>
        <RefreshCwIcon
          className={cn(
            "size-3.5",
            regenerating && "animate-spin motion-reduce:animate-none",
          )}
        />
      </Button>}
      {onMore && <Button type="button" ariaLabel={labels?.more ?? "More response actions"} onClick={onMore} className={buttonClassName} variant="ghost" size="xs" style={{ width: '1.75rem', height: '1.75rem', padding: 0 }}>
        <EllipsisIcon className="size-3.5" />
      </Button>}
      {children}
    </div>
  );
}
