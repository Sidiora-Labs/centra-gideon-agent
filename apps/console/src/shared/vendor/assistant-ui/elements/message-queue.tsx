"use client";

import type { ComponentProps } from "react";
import { ArrowUpIcon, PencilIcon, SquareIcon, XIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { field, ghostButton, mono, paper } from "./surfaces";

export interface QueuedMessage {
  id: string;
  text: string;
}

export interface MessageQueueLabels {
  running?: string;
  queuedCount?: (count: number) => string;
  stopCurrent?: string;
  sendNext?: (text: string) => string;
  edit?: (text: string) => string;
  remove?: (text: string) => string;
}

export function MessageQueue({
  running,
  queued,
  queueHint,
  onCancel,
  onEdit,
  onInterrupt,
  onInterruptQueued,
  labels,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "running" | "queued" | "queueHint" | "onCancel" | "onEdit" | "onInterrupt" | "onInterruptQueued" | "labels"
> & {
  running?: string;
  queued: readonly QueuedMessage[];
  queueHint?: string;
  onCancel?: (id: string) => void;
  onEdit?: (id: string) => void;
  onInterrupt?: () => void;
  onInterruptQueued?: (id: string) => void;
  labels?: MessageQueueLabels;
}) {
  return (
    <div
      data-slot="message-queue"
      className={cn("flex w-full max-w-sm flex-col gap-2", className)}

      {...props}
    >
      {running && <div className={cn(paper, "flex items-center gap-2.5 rounded-2xl p-3")}>
        <span className="relative flex size-2 shrink-0">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-blue-500/60 motion-reduce:hidden" />
          <span className="relative inline-flex size-2 rounded-full bg-blue-500 dark:bg-blue-400" />
        </span>
        <span className="text-foreground/90 min-w-0 flex-1 truncate text-[13.5px]">
          {running}
        </span>
        <span className={cn(mono, "text-foreground/35 shrink-0")}>{labels?.running ?? "running"}</span>
        {onInterrupt && <button type="button" aria-label={labels?.stopCurrent ?? "Stop current response"} onClick={onInterrupt}
          className={cn(ghostButton, "size-6 shrink-0")}>
          <SquareIcon className="size-3.5" />
        </button>}
      </div>}

      {queued.length > 0 && (
        <div className="flex items-baseline justify-between px-1">
          <span className={cn(mono, "text-foreground/35")}>
            {labels?.queuedCount?.(queued.length) ?? `${queued.length} queued`}
          </span>
          <span className={cn(mono, "text-foreground/35")}>
            {queueHint ?? (running ? "sends when this finishes" : "Waiting to send")}
          </span>
        </div>
      )}

      <ul className="flex flex-col gap-1.5">
        {queued.map((message, index) => (
          <li
            key={message.id}
            className={cn(
              field,
              "fade-in slide-in-from-bottom-1 animate-in fill-mode-both flex items-center gap-2.5 rounded-2xl py-2 pr-2 pl-3 duration-300",
            )}
          >
            <span
              className={cn(
                mono,
                "text-foreground/30 w-3 shrink-0 tabular-nums",
              )}
            >
              {index + 1}
            </span>
            <span className="text-foreground/60 min-w-0 flex-1 truncate text-[13.5px]">
              {message.text}
            </span>
            {onInterruptQueued ? <button type="button" aria-label={labels?.sendNext?.(message.text) ?? `Send "${message.text}" next`}
              onClick={() => onInterruptQueued(message.id)} className={cn(ghostButton, "size-6 shrink-0")}>
              <ArrowUpIcon className="size-3.5" />
            </button> : <ArrowUpIcon className="text-foreground/25 size-3 shrink-0" />}
            {onEdit && (
              <button type="button" aria-label={labels?.edit?.(message.text) ?? `Edit "${message.text}" in the queue`}
                onClick={() => onEdit(message.id)} className={cn(ghostButton, "size-6 shrink-0")}>
                <PencilIcon className="size-3.5" />
              </button>
            )}
            {onCancel && (
              <button
                type="button"
                aria-label={labels?.remove?.(message.text) ?? `Remove "${message.text}" from the queue`}
                onClick={() => onCancel(message.id)}
                className={cn(ghostButton, "size-6 shrink-0")}
              >
                <XIcon className="size-3.5" />
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
