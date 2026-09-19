import { branchIndexOf } from './branchLineage'
import { turnText, type ActivitySegment, type ApprovalSegment, type ChatTurn, type Segment, type ToolSegment } from './chatTypes'

export type SessionMarkerKind = 'turn' | 'tool' | 'subagent' | 'approval' | 'error' | 'activity'

export interface SessionMarker {
  id: string
  kind: SessionMarkerKind
  label: string
  role: 'user' | 'assistant'
  timestamp?: string
  requestExcerpt: string
  responseExcerpt: string
  turnIndex: number
  jumpIndex: number
  failedTool: boolean
}

export const LABEL_MAX = 80
export const EXCERPT_MAX = 160

const LEDGER_ACTIVITY = ['context', 'learned', 'stats']
const SUBAGENT_TOOL = /agent|task|subagent|delegate|dispatch/i

const tidy = (s: string, max = LABEL_MAX) => s.replace(/\s+/g, ' ').trim().slice(0, max)

export const isFailedTool = (seg: Segment): boolean =>
  seg.kind === 'tool' && ((seg as ToolSegment).ok === false || !!(seg as ToolSegment).agentError)

function eventMarker(seg: Segment): { kind: SessionMarkerKind; label: string } | null {
  if (seg.kind === 'tool') {
    const t = seg as ToolSegment
    const name = tidy(t.tool) || 'tool'
    return { kind: SUBAGENT_TOOL.test(name) ? 'subagent' : 'tool', label: name }
  }
  if (seg.kind === 'approval') return { kind: 'approval', label: tidy((seg as ApprovalSegment).tool) || 'approval' }
  if (seg.kind === 'error') return { kind: 'error', label: tidy((seg as { text: string }).text) || 'error' }
  if (seg.kind === 'activity') {
    const a = seg as ActivitySegment
    if (LEDGER_ACTIVITY.includes(a.activityKind || '')) return null
    const label = tidy(a.text)
    return label ? { kind: 'activity', label } : null
  }
  return null
}

export function deriveSessionMarkers(turns: ChatTurn[]): SessionMarker[] {
  const markers: SessionMarker[] = []
  const previews = turns.map(() => ({ requestExcerpt: '', responseExcerpt: '' }))
  let requestExcerpt = ''

  turns.forEach((turn, i) => {
    const text = tidy(turnText(turn), EXCERPT_MAX)
    if (turn.role === 'user') requestExcerpt = text
    previews[i] = {
      requestExcerpt,
      responseExcerpt: turn.role === 'assistant' ? text : '',
    }
  })

  let responseExcerpt = ''
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].role === 'assistant') {
      responseExcerpt = previews[i].responseExcerpt
    } else {
      previews[i].responseExcerpt = responseExcerpt
      responseExcerpt = ''
    }
  }

  turns.forEach((turn, i) => {
    const jumpIndex = branchIndexOf(turns, i)
    const failedTool = turn.segments.some(isFailedTool)
    const role = turn.role
    const preview = previews[i]
    markers.push({
      id: `t${i}`,
      kind: 'turn',
      label: tidy(turnText(turn)) || (role === 'user' ? 'You' : 'Assistant'),
      role,
      timestamp: turn.ts,
      ...preview,
      turnIndex: i,
      jumpIndex,
      failedTool,
    })
    turn.segments.forEach((seg, s) => {
      const ev = eventMarker(seg)
      if (!ev) return
      markers.push({
        id: `t${i}s${s}`,
        kind: ev.kind,
        label: ev.label,
        role,
        timestamp: turn.ts,
        ...preview,
        turnIndex: i,
        jumpIndex,
        failedTool,
      })
    })
  })
  return markers
}
