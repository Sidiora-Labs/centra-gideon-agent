
const KEY_PREFIXES = ['workflow:run:', 'workflow:', 'loop:', 'run:'] as const

export function baseContainer(key: string | null | undefined): string {
  const raw = (key ?? '').trim()
  for (const prefix of KEY_PREFIXES) {
    if (raw.startsWith(prefix)) return raw.slice(prefix.length)
  }
  return raw
}

export function keysEquivalent(
  left: string | null | undefined,
  right: string | null | undefined,
): boolean {
  const a = baseContainer(left)
  const b = baseContainer(right)
  if (!a || !b) return false
  return a === b
}

const WORKER_PREFIX = 'loop-'

export function belongsToLoop(
  sessionKey: string | null | undefined,
  loopId: string | null | undefined,
): boolean {
  const id = (loopId ?? '').trim()
  const raw = (sessionKey ?? '').trim()
  if (!id || !raw) return false
  if (raw.startsWith(WORKER_PREFIX)) {
    const rest = raw.slice(WORKER_PREFIX.length)
    return rest === id || rest.startsWith(`${id}-`)
  }
  return keysEquivalent(raw, `loop:${id}`)
}

export const KIND_TO_TEMPLATE: Readonly<Record<string, string>> = {
  general: 'general-project',
  goal: 'goal-pursuit-open-ended',
  code: 'code-project',
  design: 'design-project',
  research: 'deep-research',
}

export function templateForKind(
  kind: string | null | undefined,
  opts: { variant?: string; hasVerifyCommand?: boolean } = {},
): string {
  const normalized = (kind ?? '').trim().toLowerCase()
  if (!normalized) return ''
  if (normalized === 'goal') {
    const variant = (opts.variant ?? '').trim().toLowerCase()
    if (variant === 'verifiable') return 'goal-pursuit-verifiable'
    if (variant === 'open-ended' || variant === 'open_ended') return 'goal-pursuit-open-ended'
    if (opts.hasVerifyCommand) return 'goal-pursuit-verifiable'
  }
  return KIND_TO_TEMPLATE[normalized] ?? ''
}
