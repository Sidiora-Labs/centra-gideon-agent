import type { WorkflowRunDetailData } from '../../shared/data/api'
import { isEscalationRecord } from './EscalationPanel'

export function runEscalations(run: WorkflowRunDetailData) {
  const records = (run.escalations ?? []).filter(isEscalationRecord)
  return records.length ? records : isEscalationRecord(run.attention) ? [run.attention] : []
}

export function retryWindow(run: WorkflowRunDetailData | null | undefined): { retryAt: number } | null {
  if (!run || run.status !== 'failed') return null
  const escalations = runEscalations(run)
  if (!escalations.length) return null
  const failures = escalations.map(escalation => run.nodes.find(node => node.state === 'failed' && (
    typeof escalation.instance_path === 'string' && escalation.instance_path
      ? node.instance_path === escalation.instance_path : node.node_id === escalation.node_id
  ))?.failure)
  if (!failures.every(failure => failure?.retryable === true)) return null
  return { retryAt: Math.max(0, ...failures.map(failure => typeof failure?.retry_at === 'number' ? failure.retry_at : 0)) }
}
