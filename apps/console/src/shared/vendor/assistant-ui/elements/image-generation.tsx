"use client";

import type { ComponentProps } from "react";
import { RefreshCwIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { ghostButton, paper, ShimmerLabel } from "./surfaces";

const DOTS = Array.from({ length: 64 }, (_, i) => i);

export function ImageGeneration({
  prompt,
  generating,
  statusLabel,
  showPrompt = true,
  imageUrl,
  width,
  height,
  onRegenerate,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  prompt: string;
  generating: boolean;
  statusLabel?: string;
  showPrompt?: boolean;
  imageUrl?: string;
  width?: number;
  height?: number;
  onRegenerate?: () => void;
}) {
  const source = imageUrl && (/^https?:\/\//i.test(imageUrl) || imageUrl.startsWith("/api/artifacts/")) ? imageUrl : null;
  return (
    <div data-slot="image-generation" className={cn("flex w-52 flex-col gap-2.5", className)} {...props}>
      <div className={cn(paper, "relative flex aspect-square w-full items-center justify-center overflow-hidden rounded-2xl")}>
        {generating && <div className="absolute inset-0 grid grid-cols-8 place-items-center p-6" aria-hidden>
          {DOTS.map((dot) => <span key={dot} className="bg-foreground/20 size-1 animate-pulse rounded-full motion-reduce:animate-none"
            style={{ animationDelay: `${(Math.floor(dot / 8) + dot % 8) * 90}ms` }} />)}
        </div>}
        {!generating && source && <img src={source} alt={prompt} className="max-h-full max-w-full object-contain" />}
        {!generating && !source && <span className="text-foreground/45 text-xs">Image unavailable</span>}
        {!generating && source && width && height && <span className="absolute end-2.5 top-2.5 rounded bg-background/70 px-1 font-mono text-xs">
          {width} × {height}
        </span>}
      </div>
      {(generating || showPrompt || onRegenerate) && <div className="flex items-center justify-between gap-2">
        <p className="text-foreground/45 min-w-0 flex-1 truncate text-xs">
          {generating ? <><ShimmerLabel className="relative">{statusLabel ?? "Generating"}</ShimmerLabel>
            {showPrompt && statusLabel && prompt ? <> · {prompt}</> : null}</> : showPrompt ? prompt : null}
        </p>
        {!generating && onRegenerate && <button type="button" aria-label="Regenerate image" onClick={onRegenerate}
          className={cn(ghostButton, "size-6 shrink-0")}><RefreshCwIcon className="size-3" /></button>}
      </div>}
    </div>
  );
}
