import { useInboxProposals } from './inboxProposalState'
import { Check, Pencil, Lightbulb, AlertTriangle } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { Button } from '../../shared/ui/Button'
import { Checkbox, FieldError, TextArea } from '../../shared/ui/forms'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { type InboxItem } from '../../shared/data/api'
import {
  APPLY_CASE_LABEL,
  applyCase,
  groupLabel,
  proposalOf,
} from './proposalLens'

export function ProposalsLens({ items, onChanged }: { items: InboxItem[]; onChanged: () => void }) {
  const { selected, outcomes, editing, draft, draftError, busy, selectedItems, batchOk, groups, toggle, approve, approveSelected, startEdit, saveEdit, setDraft, setEditing } = useInboxProposals(items, onChanged)

  if (items.length === 0) {
    return (
      <EmptyState
        icon={Lightbulb}
        title="No proposals"
        hint="When a skill, a workflow or an app suggests a change, it lands here for you to approve."
      />
    )
  }

  return (
    <div className="grid gap-l">
      <div className="flex flex-wrap items-center gap-m rounded-xl border border-outline/25 bg-surface-high/40 p-m">
        <Button
          size="sm"
          variant="primary"
          onClick={approveSelected}
          loading={busy}
          disabledReason={
            selectedItems.length === 0
              ? 'Select one or more proposals first'
              : !batchOk
                ? `Selection spans ${groups} different sources or kinds — approve one group at a time`
                : undefined
          }
          disabled={selectedItems.length === 0 || !batchOk}
        >
          <Check size={14} />
          {batchOk && selectedItems.length > 0
            ? `Approve ${selectedItems.length} from ${groupLabel(selectedItems[0])}`
            : 'Approve selected'}
        </Button>
        <span data-type="body-s" className="text-on-surface-low">
          {selectedItems.length > 0
            ? `${selectedItems.length} selected`
            : 'Batch approve works within one source and kind.'}
        </span>
      </div>

      <div className="flex flex-col gap-s">
        {items.map((it) => {
          const p = proposalOf(it)
          const kase = p ? applyCase(p) : ''
          const outcome = outcomes[it.id]
          return (
            <div
              key={it.id}
              className="rounded-xl border border-outline/30 px-l py-m"
              style={{ background: 'var(--color-surface-container)' }}
            >
              <div className="flex items-start gap-s">
                <Checkbox
                  checked={selected.has(it.id)}
                  onChange={() => toggle(it.id)}
                  ariaLabel={`Select proposal: ${p?.title || it.message}`}
                />
                <div className="min-w-0 flex-1">
                  <div data-type="label-m" className="truncate text-on-surface" style={fvs(500)}>
                    {p?.title || it.message}
                  </div>
                  <div data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-s text-on-surface-low">
                    <span>{groupLabel(it)}</span>
                    {kase ? (
                      <span>· {APPLY_CASE_LABEL[kase]}</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-warn">
                        <AlertTriangle size={11} /> no runnable action
                      </span>
                    )}
                  </div>
                  {p?.preview && (
                    <pre
                      data-type="body-s"
                      className={`mt-s max-h-40 overflow-auto whitespace-pre-wrap text-on-surface-var ${p.preview_kind === 'diff' ? 'font-mono' : ''}`}
                    >
                      {p.preview}
                    </pre>
                  )}

                  <div
                    role="status"
                    aria-live="polite"
                    data-type="body-s"
                    className={outcome
                      ? `mt-s ${outcome.ok ? 'text-ok' : 'text-danger'}`
                      : 'sr-only'}
                  >
                    {outcome ? (outcome.ok ? 'Applied.' : `Not applied — ${outcome.error}. Still pending.`) : ''}
                  </div>
                  {editing === it.id && p && (
                    <div className="mt-s flex flex-col gap-s">
                      <TextArea
                        value={draft}
                        onChange={setDraft}
                        rows={6}
                        mono
                        size="sm"
                        ariaLabel="Apply payload (JSON)"
                      />

                      {draftError && <FieldError>{draftError}</FieldError>}
                      <div className="flex gap-s">
                        <Button size="sm" variant="primary" onClick={() => saveEdit(it, p)} loading={busy}>
                          <Check size={14} /> Approve edited
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
                {editing !== it.id && (
                  <div className="flex shrink-0 items-center gap-s">
                    {p?.editable && (
                      <Button size="sm" variant="secondary" onClick={() => startEdit(it, p)}>
                        <Pencil size={14} /> Edit
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={() => approve(it)}
                      loading={busy}
                      disabled={!kase}
                      disabledReason={!kase ? 'This proposal declares no runnable action' : undefined}
                    >
                      <Check size={14} /> Approve
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
