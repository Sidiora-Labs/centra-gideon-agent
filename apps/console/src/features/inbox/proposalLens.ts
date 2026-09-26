import type { InboxItem, InboxProposal } from '../../shared/data/api'

type ProposalItem = Pick<InboxItem, 'refs' | 'item_kind'>
export const APPLY_CASES = ['action', 'workflow', 'skill_promotion', 'app_callback'] as const
export type ApplyCase = (typeof APPLY_CASES)[number]
export const APPLY_CASE_LABEL: Record<ApplyCase, string> = {
  action: 'Runs an action', workflow: 'Starts a workflow', skill_promotion: 'Installs a skill', app_callback: 'Calls back into the app',
}

export function proposalOf(item: Pick<InboxItem, 'refs'>): InboxProposal | null {
  const candidate = item.refs?.proposal
  if (candidate === null || typeof candidate !== 'object') return null
  const { title, apply, preview, preview_kind, provenance, expires_at, editable } = candidate as Partial<InboxProposal>
  if (typeof title !== 'string') return null
  if (apply === null || typeof apply !== 'object') return null
  return {
    title, apply: apply as InboxProposal['apply'], editable: editable === true,
    preview: typeof preview === 'string' ? preview : '', preview_kind: preview_kind === 'diff' ? 'diff' : 'text',
    provenance: typeof provenance === 'string' ? provenance : '', expires_at: expires_at ?? null,
  }
}
export function applyCase(proposal: InboxProposal): ApplyCase | '' {
  const entries = Object.entries(proposal.apply ?? {})
  if (entries.length !== 1) return ''
  return APPLY_CASES.find(candidate => candidate === entries[0][0]) ?? ''
}
export function applyTarget(proposal: InboxProposal): string {
  const kase = applyCase(proposal)
  const payload = kase ? proposal.apply[kase] : null
  if (!payload) return ''
  if (kase === 'skill_promotion') return proposal.title
  const key = kase === 'workflow' ? 'ref' : kase === 'action' ? 'provider' : 'app'
  return typeof payload[key] === 'string' ? payload[key].trim() : ''
}
export function groupKey(item: ProposalItem): string {
  return [proposalOf(item)?.provenance ?? '', item.item_kind ?? ''].join('|')
}
export function groupLabel(item: Pick<InboxItem, 'refs'>): string {
  const provenance = proposalOf(item)?.provenance
  if (!provenance) return 'unknown source'
  return provenance.slice(0, 4) === 'app:' ? `the ${provenance.substring(4)} app` : provenance
}
function collectGroups(items: ProposalItem[]): Set<string> {
  return items.reduce((groups, item) => groups.add(groupKey(item)), new Set<string>())
}
export function canBatchApprove(selected: ProposalItem[]): boolean { return collectGroups(selected).size === 1 }
export function groupCount(selected: ProposalItem[]): number { return collectGroups(selected).size }
