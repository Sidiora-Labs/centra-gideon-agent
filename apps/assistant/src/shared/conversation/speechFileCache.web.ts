export async function createCachedSpeechFile(_requestId: string, _audio: string): Promise<string> {
  throw new Error("Web speech playback uses a Blob URL instead of a cached file.");
}

export function deleteCachedSpeechFile(_uri: string): void {
  throw new Error("Web speech playback does not create cached files.");
}
