import { describe, expect, it } from "vitest";
import { createWebSpeechAudioSource } from "./speechAudioSource";

describe("web speech audio source", () => {
  it("plays real WAV bytes through a Blob URL and revokes the URL on disposal", async () => {
    const source = createWebSpeechAudioSource("UklGRg==");
    expect(source.uri.startsWith("blob:")).toBe(true);

    const response = await fetch(source.uri);
    expect(response.headers.get("content-type")).toContain("audio/wav");
    expect([...new Uint8Array(await response.arrayBuffer())]).toEqual([0x52, 0x49, 0x46, 0x46]);

    source.dispose();
    source.dispose();
    await expect(fetch(source.uri)).rejects.toThrow();
  });
});
