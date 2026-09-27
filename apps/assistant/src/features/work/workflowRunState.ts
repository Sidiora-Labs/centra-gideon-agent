import type { WorkflowReviewPayload, WorkflowTriageResult } from '../../../../console/src/shared/data/api'

export type ReviewIntent = Readonly<{
  runId: string
  decisions: Array<{ key: string; outcome: 'accept' | 'reject'; reason?: string }>
  state: 'pending' | 'complete' | 'unknown'
  receipt?: WorkflowTriageResult
  updatedAt: number
}>

const storageKey = (owner: string, runId: string) => `gideon:workflow-review:${encodeURIComponent(owner)}:${encodeURIComponent(runId)}`

export function readReviewIntent(owner: string, runId: string): ReviewIntent | null {
  try {
    const raw = localStorage.getItem(storageKey(owner, runId))
    if (!raw) return null
    const intent = JSON.parse(raw) as ReviewIntent
    return intent.runId === runId && Array.isArray(intent.decisions) ? intent : null
  } catch { return null }
}

export function writeReviewIntent(owner: string, intent: ReviewIntent): void {
  localStorage.setItem(storageKey(owner, intent.runId), JSON.stringify(intent))
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
