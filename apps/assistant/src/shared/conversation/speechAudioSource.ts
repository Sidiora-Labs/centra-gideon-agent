export type WebSpeechAudioSource = Readonly<{
  uri: string;
  dispose: () => void;
}>;

export function createWebSpeechAudioSource(base64: string): WebSpeechAudioSource {
  const binary = globalThis.atob(base64);
  const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  const source = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
  let disposed = false;
  return {
    uri: source,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      URL.revokeObjectURL(source);
    },
  };
}
