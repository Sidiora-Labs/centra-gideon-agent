"use client";

import { useMessageTiming } from "@assistant-ui/react";
import { cn } from "../lib/utils";

export function formatTimingMs(ms: number | undefined): string {
  if (ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function MessageTiming({ className, side = "right" }: {
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
}) {
  const timing = useMessageTiming();
  if (timing?.totalStreamTime === undefined) return null;
  return (
    <details data-slot="message-timing" data-side={side} className="relative">
      <summary
        data-slot="message-timing-trigger"
        aria-label="Message timing"
        className={cn("text-muted-foreground hover:bg-accent hover:text-accent-foreground cursor-pointer rounded-md p-1 font-mono text-xs tabular-nums", className)}
      >
        {formatTimingMs(timing.totalStreamTime)}
      </summary>
      <div data-slot="message-timing-popover" className="bg-popover text-popover-foreground absolute z-20 grid min-w-35 gap-1.5 rounded-md border px-3 py-2 text-xs shadow-lg">
        {timing.firstTokenTime !== undefined && <div className="flex justify-between gap-4"><span>First token</span><span>{formatTimingMs(timing.firstTokenTime)}</span></div>}
        <div className="flex justify-between gap-4"><span>Total</span><span>{formatTimingMs(timing.totalStreamTime)}</span></div>
        {timing.tokensPerSecond !== undefined && <div className="flex justify-between gap-4"><span>Speed</span><span>{timing.tokensPerSecond.toFixed(1)} tok/s</span></div>}
        <div className="flex justify-between gap-4"><span>Chunks</span><span>{timing.totalChunks}</span></div>
      </div>
    </details>
  );
}
