import { useEffect, useState } from 'react'
import { AlertTriangle, Scale, Sparkles } from 'lucide-react'
import { api, type KnowledgeConflict } from '../../shared/data/api'
import { EmptyState, ListSkeleton } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'
import { accentChip } from '../../shared/theme/accent'

export function ConflictPanel() {
  const [conflicts, setConflicts] = useState<KnowledgeConflict[] | null>(null)

  useEffect(() => {
    let alive = true
    api.knowledgeConflicts()
      .then((d) => { if (alive) setConflicts(d.conflicts) })
      .catch(() => { if (alive) setConflicts([]) })
    return () => { alive = false }
  }, [])

  if (conflicts === null) return <ListSkeleton rows={3} what="contradictions" />
  if (conflicts.length === 0) {
    return (
      <EmptyState
        icon={Scale}
        title="No contradictions recorded"
        hint="When two stored claims disagree about the same subject, both are kept and the disagreement shows up here."
      />
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {conflicts.map((c, i) => (
        <ConflictRow key={`${c.item_id}-${i}`} conflict={c} />
      ))}
    </div>
  )
}

function ConflictRow({ conflict }: { conflict: KnowledgeConflict }) {
  const proven = conflict.basis === 'deterministic'
  return (
    <div data-type="body-s" className="rounded-lg border border-outline-variant bg-surface p-3">
      <div data-type="caption" className="mb-2 flex items-center gap-2 text-on-surface-low">
        {proven
          ? <AlertTriangle size={13} className="text-warning" aria-hidden />
          : <Sparkles size={13} aria-hidden />}
        <span style={fvs(600)}>{proven ? 'Provable conflict' : 'Possible conflict'}</span>
        <span aria-hidden>·</span>
        <span>{conflict.kind}</span>
        {!proven && (
          <>
            <span aria-hidden>·</span>
            {
}
            <span>{Math.round(conflict.confidence * 100)}% confident</span>
          </>
        )}
      </div>

      <ClaimSide
        text={conflict.left_claim}
        preferred={conflict.prefer === 'left'}
        label={conflict.item_title || conflict.left_item}
      />
      <div data-type="caption" className="my-1 pl-3 text-on-surface-low">versus</div>
      <ClaimSide
        text={conflict.right_claim}
        preferred={conflict.prefer === 'right'}
        label={conflict.right_item}
      />

      {conflict.detail && (
        <div data-type="caption" className="mt-2 text-on-surface-low">{conflict.detail}</div>
      )}
      {conflict.prefer === '' && (
        <div data-type="caption" className="mt-2 text-on-surface-low">
          Both sources carry the same weight — this one needs a human call.
        </div>
      )}
    </div>
  )
}

function ClaimSide(
  { text, preferred, label }: { text: string; preferred: boolean; label: string },
) {
  return (
    <div className="flex items-start gap-2">
      <div className={`min-w-0 flex-1 ${preferred ? '' : 'text-on-surface-low'}`}>
        <div style={fvs(preferred ? 600 : 400)}>{text}</div>
        {
}
        <div data-type="caption" className="mt-0.5 truncate text-on-surface-low" title={label}>{label}</div>
      </div>
      {preferred && (
        <span
          data-type="caption" className="shrink-0 rounded px-1.5 py-0.5"
          style={accentChip}>
          higher-trust source
        </span>
      )}
    </div>
  )
}
