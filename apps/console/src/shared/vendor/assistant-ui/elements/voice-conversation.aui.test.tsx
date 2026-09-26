import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  AssistantRuntimeProvider,
  createVoiceSession,
  useAui,
  useExternalStoreRuntime,
  type RealtimeVoiceAdapter,
  type ThreadMessageLike,
  type VoiceSessionHelpers,
} from "@assistant-ui/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceConversation } from "./voice-conversation.aui";

const priorMessages: ThreadMessageLike[] = [
  { id: "typed", role: "user", content: [{ type: "text", text: "Typed message" }] },
  { id: "earlier-user", role: "user", content: [{ type: "text", text: "Earlier question" }], metadata: { modality: "voice" } },
  { id: "earlier-assistant", role: "assistant", content: [{ type: "text", text: "Earlier answer" }], metadata: { modality: "voice" } },
];

let aui: ReturnType<typeof useAui>;
let helpers: VoiceSessionHelpers;
let disconnectProvider: () => void;
let muteProvider: () => void;
let unmuteProvider: () => void;

function Runtime({ initialMessages = [] }: { initialMessages?: ThreadMessageLike[] }) {
  const [messages, updateMessages] = useState(initialMessages);
  const voice: RealtimeVoiceAdapter = {
    connect: ({ abortSignal }) => createVoiceSession({ abortSignal }, async (nextHelpers) => {
      helpers = nextHelpers;
      return { disconnect: disconnectProvider, mute: muteProvider, unmute: unmuteProvider };
    }),
  };
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message: ThreadMessageLike) => message,
    onNew: async (message) => {
      updateMessages((current) => [...current, { role: "user", content: message.content }]);
    },
    onVoiceTranscript: (message) => {
      updateMessages((current) => [...current, message]);
    },
    adapters: { voice },
  });
  return <AssistantRuntimeProvider runtime={runtime}><RuntimeAccess /><VoiceConversation /></AssistantRuntimeProvider>;
}

function RuntimeAccess() {
  aui = useAui();
  return null;
}

const connect = async () => {
  await act(async () => { aui.thread.connectVoice(); await Promise.resolve(); });
};
const running = async () => {
  await act(async () => { helpers.setStatus({ type: "running" }); helpers.emitMode("listening"); });
};
const speak = async (role: "user" | "assistant", text: string) => {
  await act(async () => { helpers.emitTranscript({ role, text, isFinal: true }); await Promise.resolve(); });
};

afterEach(() => { cleanup(); });

describe("VoiceConversation with the actual AUI voice session", () => {
  beforeEach(() => {
    disconnectProvider = vi.fn<() => void>();
    muteProvider = vi.fn<() => void>();
    unmuteProvider = vi.fn<() => void>();
  });

  it("renders the real starting, listening, and speaking session with only new voice turns", async () => {
    render(<Runtime initialMessages={priorMessages} />);
    expect(document.querySelector('[data-slot="voice-conversation"]')).toBeNull();
    await connect();
    const panel = document.querySelector<HTMLElement>('[data-slot="voice-conversation"]')!;
    expect(within(panel).getByText("Connecting")).toBeInTheDocument();
    expect(within(panel).queryByText("Earlier question")).toBeNull();
    await running();
    expect(within(panel).getByText("Listening")).toBeInTheDocument();
    await speak("user", "Hello");
    await speak("assistant", "Hi there");
    expect(within(panel).getByText("Hello")).toBeInTheDocument();
    expect(within(panel).getByText("Hi there")).toBeInTheDocument();
    expect(within(panel).queryByText("Typed message")).toBeNull();
    await speak("user", "Second question");
    expect(within(panel).queryByText("Hello")).toBeNull();
    expect(within(panel).getByText("Hi there")).toBeInTheDocument();
    expect(within(panel).getByText("Second question")).toBeInTheDocument();
    await act(async () => { helpers.emitMode("speaking"); });
    expect(within(panel).getByText("Speaking")).toBeInTheDocument();
  });

  it("routes mute, unmute, and end through the actual session controls", async () => {
    render(<Runtime />);
    await connect();
    await running();
    fireEvent.click(screen.getByRole("button", { name: "Turn the microphone off" }));
    expect(muteProvider).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Turn the microphone on" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Turn the microphone on" }));
    expect(unmuteProvider).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "End the call" }));
    expect(disconnectProvider).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-slot="voice-conversation"]')).toBeNull();
  });

  it("starts a redial with an empty transcript after the prior session ends", async () => {
    render(<Runtime />);
    await connect();
    await running();
    await speak("user", "First call");
    expect(screen.getByText("First call")).toBeInTheDocument();
    await act(async () => { aui.thread.disconnectVoice(); });
    expect(document.querySelector('[data-slot="voice-conversation"]')).toBeNull();
    await connect();
    await running();
    expect(screen.queryByText("First call")).toBeNull();
    await speak("user", "Second call");
    expect(screen.getByText("Second call")).toBeInTheDocument();
    expect(screen.queryByText("First call")).toBeNull();
  });
});
