
import { applyCoalescedFlush, insertActivity } from '../../features/chat/coalesceReducers'
import { foldReducer, emptyRunFlags, type RunFlags } from '../../features/loops/runFold'
import type { Segment } from '../../features/chat/chatTypes'

export type ChatStep =
  | { kind: 'flush'; text: string }
  | { kind: 'activity'; text: string; activityKind?: string }
  | { kind: 'boundary' }

export function replayChat(steps: ChatStep[]): { segs: Segment[]; coalescing: boolean } {
  let segs: Segment[] = []
  let coalescing = false
  for (const step of steps) {
    if (step.kind === 'flush') {
      const r = applyCoalescedFlush(segs, step.text, coalescing)
      segs = r.segs
      coalescing = r.coalescing
    } else if (step.kind === 'activity') {
      segs = insertActivity(segs, step.text, step.activityKind ?? '', coalescing)
    } else {
      coalescing = false
    }
  }
  return { segs, coalescing }
}

export function replayRun(
  steps: { event: string; data?: unknown }[],
  initial: RunFlags = emptyRunFlags(),
): RunFlags {
  let flags = initial
  for (const s of steps) flags = foldReducer(flags, s.event, s.data)
  return flags
}

export function adjacentDuplicateTextCount(segs: Segment[]): number {
  let dupes = 0
  for (let i = 1; i < segs.length; i++) {
    const a = segs[i - 1]
    const b = segs[i]
    if (a.kind === 'text' && b.kind === 'text' && a.text === b.text) dupes++
  }
  return dupes
}
