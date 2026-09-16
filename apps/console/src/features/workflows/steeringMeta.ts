import type { WorkflowNodeState } from '../../shared/data/api'


export function judgeComment(node: Pick<WorkflowNodeState, 'degraded_reason' | 'failure'>): string {
  const remediation = node.failure?.remediation?.trim()
  const cause = node.failure?.cause_plain?.trim()
  const degraded = node.degraded_reason?.trim()
  return remediation || cause || degraded || ''
}

export function steerTextFromComment(nodeLabel: string, comment: string): string {
  const text = (comment ?? '').trim()
  if (!text) return ''
  const where = (nodeLabel ?? '').trim()
  return where ? `Address this feedback on "${where}": ${text}` : `Address this feedback: ${text}`
}

export function canSteerComment(node: Pick<WorkflowNodeState, 'degraded_reason' | 'failure'>, runIsLive: boolean): boolean {
  return runIsLive && judgeComment(node) !== ''
}
