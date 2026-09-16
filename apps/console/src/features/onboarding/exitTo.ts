const handoff: { destination?: string } = {}

export function setOnboardingExit(path: string): void {
  const withoutHash = path.startsWith('#') ? path.slice(1) : path
  handoff.destination = withoutHash.startsWith('/') ? withoutHash.slice(1) : withoutHash
}
export function peekOnboardingExit(): string { return handoff.destination ?? '' }
export function clearOnboardingExit(): void { delete handoff.destination }
