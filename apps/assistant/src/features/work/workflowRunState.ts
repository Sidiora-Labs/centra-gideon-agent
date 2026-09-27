import type { WorkflowReviewPayload, WorkflowTriageResult } from '../../../../console/src/shared/data/api'

export type ReviewIntent = Readonly<{
  runId: string
  decisions: Array<{ key: string; outcome: 'accept' | 'reject'; reason?: string }>
  state: 'pending' | 'complete' | 'unknown'
  receipt?: WorkflowTriageResult
  updatedAt: number
}>

export type ReviewIntents = readonly ReviewIntent[]

const storageKey = (owner: string, runId: string) => `gideon:workflow-review:${encodeURIComponent(owner)}:${encodeURIComponent(runId)}`

export function readReviewIntents(owner: string, runId: string): ReviewIntents {
  try {
    const raw = localStorage.getItem(storageKey(owner, runId))
    if (!raw) return []
    const stored = JSON.parse(raw) as ReviewIntent | ReviewIntents
    const valid = (intent: ReviewIntent) => intent?.runId === runId
      && Array.isArray(intent.decisions) && intent.decisions.length > 0
      && ['pending', 'complete', 'unknown'].includes(intent.state)
    if (Array.isArray(stored)) return stored.filter(valid)
    return valid(stored as ReviewIntent) ? [stored as ReviewIntent] : []
  } catch { return [] }
}

export function writeReviewIntents(owner: string, runId: string, intents: ReviewIntents): void {
  localStorage.setItem(storageKey(owner, runId), JSON.stringify(intents))
}

export function timelineEvents(runId: string, detail: {
  status: string; nodes: Array<{ node_id: string; instance_path: string; state: string; attempt?: number }>
}): Array<{ id: string; label: string; status: string; href?: string }> {
  return [
    { id: runId, label: 'Workflow run', status: detail.status },
    ...detail.nodes.map(node => ({ id: `${runId}:${node.instance_path}:${node.node_id}`,
      label: `${node.node_id} · ${node.instance_path}`, status: node.state,
      href: `/api/workflows/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(node.node_id)}` })),
  ]
}

export function unresolvedReviewMessage(intent: ReviewIntent | null, review: WorkflowReviewPayload | null): string {
  if (!intent) return ''
  if (intent.state === 'complete' && intent.receipt) return `Review recorded for run ${intent.runId}. Receipt: ${intent.receipt.receipt.reason}.`
  if (intent.state === 'unknown') return `The review request for run ${intent.runId} may have reached Gideon, but its outcome was not received. It will not be replayed automatically. Refresh native run data before deciding what to do.`
  return review ? `Review decision for run ${intent.runId} is still being checked.` : `Review decision for run ${intent.runId} is pending; native status is unavailable.`
}
