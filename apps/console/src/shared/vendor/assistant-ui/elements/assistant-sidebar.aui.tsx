"use client";

import type { PropsWithChildren, ReactNode } from "react";
import { ThreadTranscript } from "./thread.aui";

export function AssistantSidebar({ children, thread }: PropsWithChildren<{ thread?: ReactNode }>) {
  return (
    <div data-slot="aui_assistant-sidebar" className="flex h-full min-w-0">
      <div className="min-w-0 flex-1">{children}</div>
      <div className="min-w-0 flex-1 border-s">
        {thread === undefined ? <ThreadTranscript /> : thread}
      </div>
    </div>
  );
}
