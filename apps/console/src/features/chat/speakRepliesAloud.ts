const ownerKey = (session: string) => `gideon:speech-owner:v1:${session}`
const spokenKey = (session: string, assistantMessageId: string) =>
  `gideon:speech-complete:v1:${session}:${assistantMessageId}`

export type SpeechTurnOutcome = 'complete' | 'stopped' | 'error' | null | undefined

export function sessionSpeechStorage(): Storage | null {
  try { return window.sessionStorage } catch { return null }
}

export function rememberSpeechOwner(storage: Storage | null, session: string, clientTs: string): void {
  if (!storage || !session || !clientTs) return
  storage.setItem(ownerKey(session), clientTs)
}

export function claimCompletedReplySpeech(
  storage: Storage | null,
  session: string,
  clientTs: string,
  assistantMessageId: string,
  outcome: SpeechTurnOutcome,
): boolean {
  if (!storage || !session || !clientTs || !assistantMessageId) return false
  const currentOwner = ownerKey(session)
  if (storage.getItem(currentOwner) !== clientTs) return false
  if (outcome !== 'complete') {
    storage.removeItem(currentOwner)
    return false
  }
  const completed = spokenKey(session, assistantMessageId)
  if (storage.getItem(completed)) {
    storage.removeItem(currentOwner)
    return false
  }
  storage.setItem(completed, clientTs)
  storage.removeItem(currentOwner)
  return true
}

export function forgetSpeechOwner(storage: Storage | null, session: string, clientTs: string): void {
  if (!storage) return
  const key = ownerKey(session)
  if (storage.getItem(key) === clientTs) storage.removeItem(key)
}
