import type { HypermidDiagnosticsWire, HypermidLogEntryWire, HypermidLogPageWire } from '../../shared/data/api'

export interface HypermidLogBuffer {
  entries: HypermidLogEntryWire[]
  cursor?: HypermidLogPageWire['cursor']
  gap: boolean
  recoveryCursor: HypermidLogPageWire['recovery_cursor']
}

export function retainDiagnostics(
  current: HypermidDiagnosticsWire | undefined,
  next: HypermidDiagnosticsWire | undefined,
): HypermidDiagnosticsWire | undefined {
  return next ?? current
}

export function mergeLogPage(current: HypermidLogBuffer, page: HypermidLogPageWire): HypermidLogBuffer {
  const entries = page.gap ? page.entries : [...current.entries, ...page.entries]
  const unique = new Map(entries.map((entry) => [`${entry.cursor.epoch}:${entry.cursor.sequence}`, entry]))
  return {
    entries: [...unique.values()].sort((left, right) => left.cursor.epoch - right.cursor.epoch || left.cursor.sequence - right.cursor.sequence),
    cursor: page.cursor,
    gap: current.gap || page.gap,
    recoveryCursor: page.recovery_cursor ?? current.recoveryCursor,
  }
}
