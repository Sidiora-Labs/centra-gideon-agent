import { useMemo, useReducer, useRef } from 'react'
import { api, type InboxItem, type InboxProposal } from '../../shared/data/api'
import { canBatchApprove, groupCount } from './proposalLens'

type Outcome = { ok: boolean; error?: string }
type ProposalState = { selected: Set<string>; outcomes: Record<string, Outcome>; editing: string | null; draft: string; draftError: string; busy: boolean }
type Change = Partial<ProposalState> | ((state: ProposalState) => Partial<ProposalState>)
const initial = (): ProposalState => ({ selected: new Set(), outcomes: {}, editing: null, draft: '', draftError: '', busy: false })
export function useInboxProposals(items: InboxItem[], onChanged: () => void) {
  const [state, update] = useReducer((state: ProposalState, change: Change) => ({ ...state, ...(typeof change === 'function' ? change(state) : change) }), undefined, initial)
  const pending = useRef(false)
  const selectedItems = useMemo(() => items.filter(item => state.selected.has(item.id)), [items, state.selected])
  const batchOk = canBatchApprove(selectedItems)
  const apply = async (item: InboxItem, edited?: InboxProposal) => {
    let outcome: Outcome
    try { const response = await api.applyInboxProposal(item.id, edited); outcome = { ok: Boolean(response.ok), error: response.error } }
    catch (error) { outcome = { ok: false, error: error instanceof Error ? error.message : 'apply failed' } }
    update(previous => ({ outcomes: { ...previous.outcomes, [item.id]: outcome } }))
  }
  const execute = async (entries: Array<{ item: InboxItem; edited?: InboxProposal }>, closeEditor = false, clearSelection = false) => {
    if (pending.current) return
    pending.current = true; update({ busy: true })
    try {
      for (const entry of entries) await apply(entry.item, entry.edited)
      update({ ...(closeEditor ? { editing: null } : {}), ...(clearSelection ? { selected: new Set<string>() } : {}) })
      onChanged()
    } finally { pending.current = false; update({ busy: false }) }
  }
  const saveEdit = (item: InboxItem, proposal: InboxProposal) => {
    let apply: unknown
    try { apply = JSON.parse(state.draft) }
    catch { update({ draftError: 'Not valid JSON — fix it before approving.' }); return }
    if (!apply || typeof apply !== 'object' || Array.isArray(apply)) { update({ draftError: 'The apply payload must be an object.' }); return }
    void execute([{ item, edited: { ...proposal, apply: apply as InboxProposal['apply'] } }], true)
  }
  return { ...state, selectedItems, batchOk, groups: groupCount(selectedItems),
    toggle: (id: string) => update(previous => { const selected = new Set(previous.selected); if (!selected.delete(id)) selected.add(id); return { selected } }),
    setDraft: (draft: string) => update({ draft }), setEditing: (editing: string | null) => update({ editing }),
    startEdit: (item: InboxItem, proposal: InboxProposal) => update({ editing: item.id, draftError: '', draft: JSON.stringify(proposal.apply, null, 2) }),
    approve: (item: InboxItem) => execute([{ item }]), approveSelected: () => { if (batchOk) void execute(selectedItems.map(item => ({ item })), false, true) }, saveEdit,
  }
}
