const handoff: { destination?: string } = {}
const KEY = 'gideon:onboarding:return'

export function setOnboardingExit(path: string): void {
  const withoutHash = path.startsWith('#') ? path.slice(1) : path
  handoff.destination = withoutHash.startsWith('/') ? withoutHash.slice(1) : withoutHash
  try { sessionStorage.setItem(KEY, handoff.destination) } catch { /* Storage is optional. */ }
}
export function peekOnboardingExit(): string {
  if (handoff.destination) return handoff.destination
  try { return sessionStorage.getItem(KEY) ?? '' } catch { return '' }
}
export function clearOnboardingExit(): void {
  delete handoff.destination
  try { sessionStorage.removeItem(KEY) } catch { /* Storage is optional. */ }
}
