"use client";

import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { ThreadTranscript } from "./thread.aui";
import { ThreadList } from "./thread-list.aui";

export type AssistantModalProps = {
  thread?: ReactNode;
  history?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: ReactNode;
};

export function AssistantModal({ thread, history, open, onOpenChange, trigger }: AssistantModalProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const [view, setView] = useState<"thread" | "list">("thread");
  const currentOpen = open ?? internalOpen;
  const changeOpen = (next: boolean) => {
    if (open === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  };
  const content = (
    <>
      <div data-slot="aui_assistant-modal-header" className="flex shrink-0 gap-2 border-b p-2">
        <button type="button" onClick={() => setView("thread")} aria-pressed={view === "thread"}>Conversation</button>
        <button type="button" onClick={() => setView("list")} aria-pressed={view === "list"}>History</button>
        <button type="button" className="ml-auto" onClick={() => changeOpen(false)} aria-label="Close assistant">Close</button>
      </div>
      <div data-slot="aui_assistant-modal-body" className="min-h-0 flex-1 overflow-auto">
        {view === "thread" ? (thread === undefined ? <ThreadTranscript /> : thread)
          : (history === undefined ? <ThreadList /> : history)}
      </div>
    </>
  );

  if (trigger === null) {
    if (!currentOpen || typeof document === "undefined") return null;
    return createPortal(
      <div role="dialog" aria-label="Assistant" data-slot="aui_assistant-modal"
        className="bg-background border-border fixed inset-x-3 bottom-3 z-50 flex h-[min(80vh,40rem)] flex-col overflow-hidden rounded-xl border shadow-xl">
        {content}
      </div>, document.body,
    );
  }

  return (
    <PopoverPrimitive.Root open={currentOpen} onOpenChange={changeOpen}>
      <PopoverPrimitive.Trigger aria-label="Open assistant">{trigger ?? "Assistant"}</PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner side="top" align="end" sideOffset={16}>
          <PopoverPrimitive.Popup data-slot="aui_assistant-modal" className="bg-background border-border flex h-[min(80vh,40rem)] w-[min(90vw,36rem)] flex-col overflow-hidden rounded-xl border shadow-xl">
            {content}
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
