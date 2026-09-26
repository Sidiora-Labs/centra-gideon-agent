"use client";

import {
  type ComponentProps,
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { ArrowDownIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { field, floating, paper } from "./surfaces";

export interface ScrollAnchorMessage {
  role: "user" | "assistant";
  text: string;
}

const INITIAL_COUNT = 3;
const APPEND_MS = 1300;

export function ScrollAnchor({
  messages = [],
  viewportRef: controlledViewportRef,
  pinned: controlledPinned,
  unreadCount,
  showJump,
  onJump,
  paused = false,
  onSettled,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "messages" | "paused" | "onSettled" | "viewportRef" | "pinned" | "unreadCount" | "showJump" | "onJump"
> & {
  messages?: ScrollAnchorMessage[];
  viewportRef?: RefObject<HTMLDivElement | null>;
  pinned?: boolean;
  unreadCount?: number;
  showJump?: boolean;
  onJump?: () => void;
  paused?: boolean;
  onSettled?: () => void;
}) {
  const demoViewportRef = useRef<HTMLDivElement>(null);
  const controlled = controlledViewportRef !== undefined;
  const viewportRef = controlledViewportRef ?? demoViewportRef;
  const [count, setCount] = useState(INITIAL_COUNT);
  const [pinned, setPinned] = useState(true);
  const [seenCount, setSeenCount] = useState(INITIAL_COUNT);

  useEffect(() => {
    if (controlled || paused) return;
    const id = setInterval(() => {
      setCount((current) => {
        if (current >= messages.length) return current;
        return current + 1;
      });
    }, APPEND_MS);
    return () => clearInterval(id);
  }, [controlled, messages.length, paused]);

  useEffect(() => {
    if (!controlled && count >= messages.length && pinned) onSettled?.();
  }, [controlled, count, pinned, messages.length, onSettled]);

  useEffect(() => {
    if (!controlled && count === INITIAL_COUNT + 1) {
      // The unpin lands one commit after the count that triggers it, so the
      // pinned scroll still runs for that message.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPinned(false);
      setSeenCount(count);
      const viewport = viewportRef.current;
      if (viewport) viewport.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [controlled, count, viewportRef]);

  useEffect(() => {
    if (controlled || !pinned) return;
    const viewport = viewportRef.current;
    if (viewport) viewport.scrollTo({ top: viewport.scrollHeight });
  }, [controlled, count, pinned, viewportRef]);

  const jump = useCallback(() => {
    const viewport = viewportRef.current;
    if (viewport) {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    }
    setPinned(true);
    setSeenCount(count);
  }, [controlled, count, viewportRef]);

  useEffect(() => {
    if (controlled || paused || pinned || count - seenCount < 2) return;
    const id = setTimeout(jump, 2400);
    return () => clearTimeout(id);
  }, [controlled, paused, pinned, count, seenCount, jump]);

  const newCount = pinned ? 0 : count - seenCount;
  const visibleCount = controlled ? (controlledPinned ? 0 : Math.max(0, unreadCount ?? 0)) : newCount;
  const handleJump = controlled ? onJump : jump;
  const jumpVisible = controlled ? !controlledPinned && (showJump ?? visibleCount > 0) : visibleCount > 0;

  return (
    <div
      data-slot="scroll-anchor"
      className={cn(
        controlled ? "pointer-events-none w-full" : cn(paper, "relative h-64 w-full max-w-sm overflow-hidden rounded-2xl"),
        className,
      )}

      {...props}
    >
      {!controlled && <div
        ref={demoViewportRef}
        className="flex h-full flex-col gap-2.5 overflow-y-hidden scroll-smooth p-4"
      >
        {messages.slice(0, count).map((message, i) => (
          <div
            key={i}
            className={cn(
              "fade-in slide-in-from-bottom-1 animate-in max-w-[85%] text-xs leading-relaxed duration-300 motion-reduce:animate-none",
              message.role === "user"
                ? cn(field, "self-end rounded-2xl px-3 py-1.5")
                : "text-foreground/55 self-start",
            )}
          >
            {message.text}
          </div>
        ))}
      </div>}
      {!controlled && <div
        aria-hidden
        className="from-background dark:from-popover pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b to-transparent"
      />}
      {jumpVisible && (
        <button
          type="button"
          onClick={handleJump}
          className={cn(
            floating,
            "fade-in slide-in-from-bottom-2 animate-in mx-auto flex w-fit items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs transition-transform duration-200 hover:-translate-y-px",
            controlled ? "pointer-events-auto" : "absolute inset-x-0 bottom-3",
          )}
        >
          <ArrowDownIcon className="size-3 opacity-60" />
          {visibleCount > 0 ? `${visibleCount} new ${visibleCount === 1 ? "message" : "messages"}` : "Jump to latest"}
        </button>
      )}
    </div>
  );
}
