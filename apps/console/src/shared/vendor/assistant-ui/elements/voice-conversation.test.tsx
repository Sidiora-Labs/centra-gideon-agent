import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VoiceConversation, type VoiceMode } from "./voice-conversation";

const turns = [
  { id: "user-1", role: "user" as const, text: "Can you hear me?" },
  { id: "assistant-1", role: "assistant" as const, text: "Yes." },
];

const rings = () => {
  const control = screen.getByRole("button", { name: "Interrupt the assistant" });
  return [...control.querySelectorAll<HTMLElement>("span")];
};

describe("VoiceConversation measured amplitude", () => {
  it("keeps the donor's actual zero and nonzero scaling", () => {
    const { rerender } = render(<VoiceConversation mode="listening" amplitude={0} transcript={turns} />);
    expect(rings().map((ring) => ring.style.transform)).toEqual(["scale(0.72)", "scale(0.8)", "scale(0.9)"]);
    expect(screen.getByText("Can you hear me?")).toBeInTheDocument();
    expect(screen.getByText("Yes.")).toBeInTheDocument();
    expect(screen.getByText("Listening for you")).toBeInTheDocument();

    rerender(<VoiceConversation mode="speaking" amplitude={0.5} transcript={turns} />);
    expect(rings().map((ring) => ring.style.transform)).toEqual(["scale(0.86)", "scale(0.91)", "scale(1)"]);
    expect(screen.getByText("Speaking")).toBeInTheDocument();

    rerender(<VoiceConversation mode="speaking" amplitude={2} transcript={turns} />);
    expect(rings().map((ring) => ring.style.transform)).toEqual(["scale(1)", "scale(1.02)", "scale(1.1)"]);
  });

  it("keeps neutral rings through mode transitions when amplitude is unknown", () => {
    const onInterrupt = vi.fn();
    const onToggleMute = vi.fn();
    const onEnd = vi.fn();
    const { rerender } = render(<VoiceConversation mode="connecting" transcript={turns} onInterrupt={onInterrupt} onToggleMute={onToggleMute} onEnd={onEnd} />);
    const expectNeutral = (mode: VoiceMode) => {
      expect(screen.getByText(mode === "connecting" ? "Connecting" : mode === "listening" ? "Listening" : mode === "thinking" ? "Thinking" : "Speaking")).toBeInTheDocument();
      expect(rings().every((ring) => ring.style.transform === "")).toBe(true);
    };
    expectNeutral("connecting");
    expect(screen.getByRole("button", { name: "Interrupt the assistant" })).toBeDisabled();

    rerender(<VoiceConversation mode="listening" transcript={turns} onInterrupt={onInterrupt} onToggleMute={onToggleMute} onEnd={onEnd} />);
    expectNeutral("listening");
    rerender(<VoiceConversation mode="thinking" transcript={turns} onInterrupt={onInterrupt} onToggleMute={onToggleMute} onEnd={onEnd} />);
    expectNeutral("thinking");
    rerender(<VoiceConversation mode="speaking" transcript={turns} onInterrupt={onInterrupt} onToggleMute={onToggleMute} onEnd={onEnd} />);
    expectNeutral("speaking");
    expect(screen.getByText("Tap to interrupt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Interrupt the assistant" }));
    expect(onInterrupt).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Turn the microphone off" }));
    fireEvent.click(screen.getByRole("button", { name: "End the call" }));
    expect(onToggleMute).toHaveBeenCalledOnce();
    expect(onEnd).toHaveBeenCalledOnce();
  });

  it("treats nonfinite levels as unknown and retains the muted status", () => {
    const { rerender } = render(<VoiceConversation mode="speaking" amplitude={Number.NaN} transcript={[]} muted />);
    expect(rings().every((ring) => ring.style.transform === "")).toBe(true);
    expect(screen.getByText("Mic off")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn the microphone on" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "End the call" })).toBeDisabled();
    rerender(<VoiceConversation mode="listening" amplitude={Number.POSITIVE_INFINITY} transcript={[]} />);
    expect(rings().every((ring) => ring.style.transform === "")).toBe(true);
    expect(within(screen.getByRole("button", { name: "Interrupt the assistant" })).queryAllByRole("progressbar")).toHaveLength(0);
  });
});
