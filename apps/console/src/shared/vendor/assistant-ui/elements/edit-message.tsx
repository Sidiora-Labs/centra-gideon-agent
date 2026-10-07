"use client";


import { Button } from "../../../ui/Button";
import type { ComponentProps } from "react";
import { AlertTriangleIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { field, inkButton, mono, paper } from "./surfaces";

export interface EditMessageLabels {
  edit?: string;
  cancel?: string;
  send?: string;
  discardedReplies?: (count: number) => string;
}

export function EditMessage({
  value,
  discardedReplies,
  editing,
  onValueChange,
  onSave,
  onCancel,
  onStartEdit,
  labels,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  | "children"
  | "value"
  | "discardedReplies"
  | "editing"
  | "onValueChange"
  | "onSave"
  | "onCancel"
  | "onStartEdit"
  | "labels"
> & {
  value: string;
  discardedReplies: number;
  editing: boolean;
  onValueChange?: (value: string) => void;
  onSave?: () => void;
  onCancel?: () => void;
  onStartEdit?: () => void;
  labels?: EditMessageLabels;
}) {
  if (!editing) {
    return (
      <div
        data-slot="edit-message"
        className={cn("flex w-full max-w-sm justify-end", className)}
        {...props}
      >
        <Button type="button" onClick={onStartEdit} disabledReason={(!onStartEdit) ? "Editing is unavailable for this message." : undefined} title={(!onStartEdit) ? "Editing is unavailable for this message." : undefined} className={cn(
            "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
            field,
            "hover:bg-foreground/[0.07] max-w-[85%] rounded-2xl px-3.5 py-2.5 text-start text-[13.5px] transition-colors",
          )} disabled={(!onStartEdit) || undefined} variant="ghost" size="sm" style={{ height: 'auto', paddingInline: '0.875rem', paddingBlock: '0.625rem' }}>
          {value}
        </Button>
      </div>
    );
  }

  return (
    <div
      data-slot="edit-message"
      className={cn(
        paper,
        "flex w-full max-w-sm flex-col gap-3 rounded-[20px] p-3.5",
        className,
      )}

      {...props}
    >
      <textarea
        value={value}
        onChange={(event) => onValueChange?.(event.target.value)}
        disabled={!onValueChange}
        rows={2}
        aria-label={labels?.edit ?? "Edit your message"}
        className={cn(
          field,
          "text-foreground/90 focus-visible:ring-foreground/20 resize-none rounded-xl px-3 py-2.5 text-[13.5px] leading-relaxed outline-none focus-visible:ring-1",
        )}
      />

      {discardedReplies > 0 && (
        <div className="flex items-center gap-2 text-amber-700 dark:text-amber-400">
          <AlertTriangleIcon className="size-3.5 shrink-0" />
          <span className={cn(mono, "tabular-nums")}>
            {labels?.discardedReplies?.(discardedReplies) ??
              `sending discards ${discardedReplies} ${discardedReplies === 1 ? "reply" : "replies"}`}
          </span>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button type="button" onClick={onCancel} disabledReason={(!onCancel) ? "Canceling this edit is unavailable." : undefined} title={(!onCancel) ? "Canceling this edit is unavailable." : undefined} className="aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100 text-foreground/55 hover:bg-foreground/[0.06] hover:text-foreground/90 h-8 rounded-full px-3.5 text-xs font-medium transition-[background-color,color,scale] duration-150 active:scale-[0.96]" disabled={(!onCancel) || undefined} variant="ghost" size="sm" style={{ height: '2rem', paddingInline: '0.875rem' }}>
          {labels?.cancel ?? "Cancel"}
        </Button>
        <Button type="button" onClick={onSave} disabledReason={(!onSave) ? "Saving this edit is unavailable." : undefined} title={(!onSave) ? "Saving this edit is unavailable." : undefined} className={cn(
            "aria-disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:hover:bg-transparent aria-disabled:active:scale-100",
            inkButton,
            "flex h-8 items-center rounded-full px-3.5 text-xs font-medium",
          )} disabled={(!onSave) || undefined} variant="ghost" size="sm" style={{ height: '2rem', paddingInline: '0.875rem' }}>
          {labels?.send ?? "Send"}
        </Button>
      </div>
    </div>
  );
}
