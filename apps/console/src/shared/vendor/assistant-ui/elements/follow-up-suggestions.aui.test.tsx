import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  ThreadPrimitive,
  useAui,
  useExternalStoreRuntime,
  type ThreadMessageLike,
  type ThreadSuggestion,
} from "@assistant-ui/react";
import { useEffect, useState, type ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ThreadFollowupSuggestions } from "./follow-up-suggestions.aui";

type StoredMessage = { id: string; role: "user" | "assistant"; text: string };
const answer: StoredMessage = { id: "answer", role: "assistant", text: "Ready" };
const donorSuggestions: ThreadSuggestion[] = [
  { title: "Weather", label: "in SF", prompt: "What is the weather in San Francisco today?" },
  { label: "", prompt: "Summarize this" },
];

let setSuggestions: (suggestions: ThreadSuggestion[]) => void;
let setMessages: (messages: StoredMessage[]) => void;
let setRunning: (running: boolean) => void;
let readComposer: () => string;
let sentPrompts: string[];

function ComposerProbe() {
  const aui = useAui();
  useEffect(() => { readComposer = () => aui.composer.getState().text; }, [aui]);
  return null;
}

function Runtime({ children }: { children: ReactNode }) {
  const [messages, updateMessages] = useState<StoredMessage[]>([answer]);
  const [suggestions, updateSuggestions] = useState<ThreadSuggestion[]>(donorSuggestions);
  const [isRunning, updateRunning] = useState(false);
  setSuggestions = updateSuggestions;
  setMessages = updateMessages;
  setRunning = updateRunning;
  const runtime = useExternalStoreRuntime({
    messages,
    suggestions,
    isRunning,
    convertMessage: (message: StoredMessage): ThreadMessageLike => ({
      id: message.id,
      role: message.role,
      content: [{ type: "text", text: message.text }],
    }),
    onNew: async (message) => {
      const text = message.content.map((part) => part.type === "text" ? part.text : "").join("");
      sentPrompts.push(text);
      updateMessages((current) => [...current, { id: `sent-${current.length}`, role: "user", text }]);
      updateSuggestions([]);
    },
  });
  return <AssistantRuntimeProvider runtime={runtime}><ThreadPrimitive.Root><ComposerProbe />{children}</ThreadPrimitive.Root></AssistantRuntimeProvider>;
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(() => {
  cleanup();
  sentPrompts = [];
});

describe("ThreadFollowupSuggestions with the actual AUI runtime", () => {
  it("shows donor title and label but sends the full prompt through the external store", async () => {
    sentPrompts = [];
    const onPick = vi.fn();
    render(<Runtime><ThreadFollowupSuggestions onPick={onPick} /></Runtime>);
    const rich = screen.getByRole("button", { name: "Weatherin SF" });
    expect(rich.querySelector(".aui-thread-followup-suggestion-label")).toHaveTextContent("in SF");
    expect(screen.getByRole("button", { name: "Summarize this" })).toBeInTheDocument();
    await act(async () => { fireEvent.click(rich); });
    expect(onPick).toHaveBeenCalledExactlyOnceWith("What is the weather in San Francisco today?");
    expect(sentPrompts).toEqual(["What is the weather in San Francisco today?"]);
    expect(screen.queryByRole("button", { name: "Weatherin SF" })).toBeNull();
  });

  it("edits the real composer without sending when sendOnSelect is false", () => {
    sentPrompts = [];
    const onPick = vi.fn();
    render(<Runtime><ThreadFollowupSuggestions onPick={onPick} sendOnSelect={false} /></Runtime>);
    fireEvent.click(screen.getByRole("button", { name: "Summarize this" }));
    expect(readComposer()).toBe("Summarize this");
    expect(sentPrompts).toEqual([]);
    expect(onPick).toHaveBeenCalledExactlyOnceWith("Summarize this");
    expect(screen.getByRole("button", { name: "Weatherin SF" })).toBeInTheDocument();
  });

  it("renders only when the real thread is nonempty, idle, and has suggestions", () => {
    sentPrompts = [];
    render(<Runtime><ThreadFollowupSuggestions /></Runtime>);
    expect(screen.getAllByRole("button")).toHaveLength(2);
    act(() => setSuggestions([]));
    expect(screen.queryByRole("button")).toBeNull();
    act(() => setSuggestions(donorSuggestions));
    expect(screen.getAllByRole("button")).toHaveLength(2);
    act(() => setRunning(true));
    expect(screen.queryByRole("button")).toBeNull();
    act(() => setRunning(false));
    expect(screen.getAllByRole("button")).toHaveLength(2);
    act(() => setMessages([]));
    expect(screen.queryByRole("button")).toBeNull();
    act(() => setMessages([answer]));
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });
});
