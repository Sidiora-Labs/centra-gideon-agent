import {
  cacheDirectory,
  deleteAsync,
  EncodingType,
  writeAsStringAsync,
} from "expo-file-system/legacy";

export async function createCachedSpeechFile(requestId: string, audio: string): Promise<string> {
  if (!cacheDirectory) throw new Error("Speech cache is unavailable");
  const safeRequestId = requestId.replace(/[^a-zA-Z0-9_-]/g, "_");
  const uri = `${cacheDirectory}gideon-speech-${safeRequestId}.wav`;
  try {
    await writeAsStringAsync(uri, audio, { encoding: EncodingType.Base64 });
    return uri;
  } catch (error) {
    await deleteAsync(uri, { idempotent: true }).catch(() => {});
    throw error;
  }
}

export function deleteCachedSpeechFile(uri: string): void {
  void deleteAsync(uri, { idempotent: true }).catch(() => {});
}
