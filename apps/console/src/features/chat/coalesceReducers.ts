import type { Segment } from './chatTypes'


export function applyCoalescedFlush(
  segs: Segment[],
  revealed: string,
  coalescing: boolean,
): { segs: Segment[]; coalescing: boolean } {
  const next = segs.slice()
  const last = next[next.length - 1]
  if (coalescing && last && last.kind === 'text') {
    next[next.length - 1] = { kind: 'text', text: revealed }
  } else {
    next.push({ kind: 'text', text: revealed })
  }
  return { segs: next, coalescing: true }
}

export function insertActivity(
  segs: Segment[],
  text: string,
  activityKind: string,
  coalescing: boolean,
): Segment[] {
  if (segs.some((sg) => sg.kind === 'tool')) return segs
  const insertAt = (coalescing && segs[segs.length - 1]?.kind === 'text') ? segs.length - 1 : segs.length
  const neighbor = segs[insertAt - 1] ?? segs[insertAt]
  if (neighbor && neighbor.kind === 'activity' && neighbor.text === text) return segs
  const next = segs.slice()
  next.splice(insertAt, 0, { kind: 'activity', text, activityKind })
  return next
}
