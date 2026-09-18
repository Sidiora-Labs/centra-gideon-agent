import type { BrowseKillState, BrowseStatus } from '../../../shared/data/api'

export interface BrowseExpiredNotice {
  site: string
  key_present?: boolean
}

export interface BrowseStepView {
  run_id: string
  step_n: number
  url: string
  action: string
  screenshot: string
  note: string
}

export interface BrowseMirrorSnapshot {
  step: BrowseStepView | null
  kill: BrowseKillState
  expired: BrowseExpiredNotice[]
}

export const BROWSE_STEP = 'browse_step'
export const BROWSE_KILL = 'browse_kill'
export const BROWSE_AUTH_EXPIRED = 'browse_auth_expired'

const NO_KILL: BrowseKillState = { active: false, reason: '', started_at: '' }

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const int = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const fields = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' ? (v as Record<string, unknown>) : {}

export function readStepFrame(data: unknown): BrowseStepView | null {
  const d = fields(data)
  const url = str(d.url)
  const action = str(d.action)
  if (!url && !action) return null
  return {
    run_id: str(d.run_id),
    step_n: int(d.step_n),
    url,
    action,
    screenshot: str(d.screenshot),
    note: str(d.note),
  }
}

export function readKillFrame(data: unknown): BrowseKillState {
  const d = fields(data)
  return { active: d.active === true, reason: str(d.reason), started_at: str(d.started_at) }
}

export function readExpiredSite(data: unknown): BrowseExpiredNotice | null {
  const d = fields(data)
  const site = str(d.site)
  if (!site) return null
  return typeof d.key_present === 'boolean' ? { site, key_present: d.key_present } : { site }
}

let reads = 0
let snapshot: BrowseMirrorSnapshot = { step: null, kill: NO_KILL, expired: [] }
let fromStatus: BrowseExpiredNotice[] = []
let fromSocket: Array<{ site: BrowseExpiredNotice; read: number }> = []
const listeners = new Set<() => void>()

function merge(): BrowseExpiredNotice[] {
  const out = new Map<string, BrowseExpiredNotice>()
  for (const s of fromStatus) out.set(s.site, s)
  for (const s of fromSocket) if (!out.has(s.site.site)) out.set(s.site.site, s.site)
  return [...out.values()].sort((a, b) => a.site.localeCompare(b.site))
}

function publish(next: Partial<BrowseMirrorSnapshot>): void {
  snapshot = { ...snapshot, ...next, expired: merge() }
  for (const listener of [...listeners]) listener()
}

export function subscribeBrowseMirror(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function browseMirrorSnapshot(): BrowseMirrorSnapshot {
  return snapshot
}

export function applyBrowseStep(data: unknown): void {
  const step = readStepFrame(data)
  if (step) publish({ step })
}

export function applyBrowseKill(data: unknown): void {
  publish({ kill: readKillFrame(data) })
}

export function applyBrowseAuthExpired(data: unknown): void {
  const site = readExpiredSite(data)
  if (!site) return
  fromSocket = [...fromSocket.filter((s) => s.site.site !== site.site), { site, read: reads }]
  publish({})
}

export function beginBrowseStatusRead(): number {
  return ++reads
}

export function applyBrowseStatus(status: BrowseStatus, read: number): void {
  fromStatus = (Array.isArray(status.expired) ? status.expired : [])
    .map(readExpiredSite)
    .filter((s): s is BrowseExpiredNotice => s !== null)
  fromSocket = fromSocket.filter((s) => s.read >= read)
  publish({ kill: readKillFrame(status.kill) })
}

export function resetBrowseMirror(): void {
  reads = 0
  fromStatus = []
  fromSocket = []
  snapshot = { step: null, kill: NO_KILL, expired: [] }
  for (const listener of [...listeners]) listener()
}
