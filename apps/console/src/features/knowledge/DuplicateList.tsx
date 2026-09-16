import { useState } from 'react'
import { ExternalLink, GitMerge } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FieldError } from '../../shared/ui/forms'
import { confirm } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { api, type KnowledgeDuplicate } from '../../shared/data/api'
import { relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../shared/theme/fontWeight'

export function DuplicateList({ item, duplicates, error, onRetry, onOpenItem, onMerged }: {
  item: { id: string; title?: string }
  duplicates: KnowledgeDuplicate[]
  error?: unknown
  onRetry: () => void
  onOpenItem: (id: string) => void
  onMerged: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)

  if (error) {
    return (
      <div className="flex flex-col items-start gap-1.5">
        <FieldError>
          {error instanceof Error && error.message
            ? `Couldn't check for duplicates: ${error.message}`
            : "Couldn't check for duplicates."}
        </FieldError>
        {
}
        <p data-type="caption" className="text-on-surface-low">This item may still have duplicates.</p>
        <Button variant="secondary" size="xs" onClick={onRetry}>Try again</Button>
      </div>
    )
  }

  async function merge(dup: KnowledgeDuplicate) {
    const keeper = item.title || 'this item'
    const loser = dup.title || 'the other copy'
    const dupMeta = [
      dup.word_count > 0 ? `${dup.word_count.toLocaleString()} words` : '',
      dup.created_at ? `added ${relPast(dup.created_at)}` : '',
    ].filter(Boolean).join(', ')
    const ok = await confirm({
      title: `Merge this duplicate into “${keeper}”?`,
      body: (
        <>
          <p>
            <strong>The item you have open is kept</strong> and inherits everything from the
            duplicate — its collections, tags, entity mentions and highlights.
          </p>
          <p className="mt-2">
            <strong>The duplicate is then deleted:</strong> “{loser}”
            {dupMeta && <> — {dupMeta}</>}. This cannot be undone.
          </p>
          <p className="mt-2 text-on-surface-low">
            To keep that copy instead, open it and merge from there.
          </p>
        </>
      ),
      danger: true,
      confirmLabel: 'Merge and delete',
    })
    if (!ok) return
    setBusy(dup.id)
    try {
      const res = await api.mergeKnowledgeItems(item.id, dup.id)
      const parts = [
        [res.moved?.collections ?? 0, 'collection'],
        [res.moved?.tags ?? 0, 'tag'],
        [res.moved?.mentions ?? 0, 'mention'],
        [res.moved?.annotations ?? 0, 'highlight'],
      ] as const
      const moved = parts
        .filter(([n]) => n > 0)
        .map(([n, noun]) => `${n} ${noun}${n === 1 ? '' : 's'}`)
      notify(
        moved.length
          ? `Merged “${loser}” in — ${moved.join(', ')} moved across`
          : `Merged “${loser}” in`,
        'success',
      )
      onMerged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That merge did not go through', 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <ul className="flex flex-col gap-1.5">
      {duplicates.map((dup) => (
        <li key={dup.id} className="rounded-md bg-surface-container px-m py-2">
          {
}
          <p data-type="label-s" className="truncate text-on-surface" style={fvs(500)}>
            {dup.title || '(untitled)'}
          </p>
          <div data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-on-surface-low">
            {dup.word_count > 0 && <span>{dup.word_count.toLocaleString()} words</span>}
            {dup.word_count > 0 && dup.created_at && <span aria-hidden>·</span>}
            {dup.created_at && <span>added {relPast(dup.created_at)}</span>}
          </div>
          {
}
          {dup.reason && (
            <p data-type="caption" className="mt-1 text-on-surface-var">{dup.reason}</p>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {
}
            <Button variant="ghost" size="xs" onClick={() => onOpenItem(dup.id)}
              ariaLabel={`Open “${dup.title || 'untitled item'}”`}>
              <ExternalLink size={14} aria-hidden />
              Open
            </Button>
            {
}
            <Button variant="danger" size="xs" loading={busy === dup.id}
              disabled={busy !== null} onClick={() => merge(dup)}>
              <GitMerge size={14} aria-hidden />
              {
}
              Merge into this item
            </Button>
          </div>
        </li>
      ))}
    </ul>
  )
}
