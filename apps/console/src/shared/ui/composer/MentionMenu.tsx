import { useEffect, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { Loader2 } from 'lucide-react'
import { composerMenuClass, composerOptionClass, useComposerTypeahead } from './composerTypeahead'
import { formatMentionSize, mentionResults, mentionSearchKey, searchMentions, type MentionRow } from './mentionSearch'
import { ComposerPersonItem } from '../../vendor/assistant-ui/elements/composer'

export type MentionPick =
  | { kind: 'file'; path: string; name: string }
  | { kind: 'knowledge'; id: string; name: string }
  | { kind: 'prompt'; name: string }

export function MentionMenu({ query, anchorRef, open, project, leading, onSelect, onClose, idPrefix, onActiveIndex }: {
  query: string; anchorRef: RefObject<HTMLElement | null>; open: boolean; project?: string; leading?: boolean
  onSelect: (pick: MentionPick) => void; onClose: () => void; idPrefix: string; onActiveIndex: (index: number | null) => void
}) {
  const key = mentionSearchKey(query, project, leading)
  const [answer, setAnswer] = useState<{ key: string; rows: MentionRow[]; loading: boolean }>({ key, rows: [], loading: false })
  const rows = open && query.length >= 2 && answer.key === key ? answer.rows : []
  const loading = query.length >= 2 && (answer.key !== key || answer.loading)
  useEffect(() => {
    if (!open || query.length < 2) { setAnswer({ key, rows: [], loading: false }); return }
    const cached = mentionResults.peek(key)
    if (cached !== undefined) { setAnswer({ key, rows: cached, loading: false }); return }
    let current = true
    setAnswer({ key, rows: [], loading: true })
    const timer = setTimeout(() => {
      searchMentions(query, project, leading).then(results => {
        if (current) setAnswer({ key, rows: results, loading: false })
      }).catch(() => { if (current) setAnswer({ key, rows: [], loading: false }) })
    }, 180)
    return () => { current = false; clearTimeout(timer) }
  }, [key, open, query, project, leading])
  const choose = (index: number) => {
    const row = rows[index]
    if (!row) return
    const pick: MentionPick = row.kind === 'file' ? { kind: 'file', path: row.id, name: row.name }
      : row.kind === 'knowledge' ? { kind: 'knowledge', id: row.id, name: row.name } : { kind: 'prompt', name: row.name }
    onSelect(pick)
  }
  const menu = useComposerTypeahead({ open, anchorRef, count: rows.length, identity: key, idPrefix,
    onSelect: choose, onClose, onActiveIndex, height: 300, above: 140 })
  if (!open || !anchorRef.current) return null
  const scope = leading ? 'prompts, files & knowledge' : 'files & knowledge'
  const hint = query.length < 2 ? `Type 2+ characters to search ${scope}…`
    : loading ? 'Searching…' : `No matching ${leading ? 'prompts, files or knowledge' : 'files or knowledge'}`
  return createPortal(<div ref={menu.menuRef} id={`${idPrefix}-list`} role="listbox"
    aria-label={leading ? 'Prompts, files and knowledge' : 'Files and knowledge'} className={composerMenuClass} style={menu.position}>
    {rows.length === 0 ? <div data-type="caption" className="px-3 py-3 text-center text-on-surface-low">
      {loading && query.length >= 2 && <Loader2 size={12} className="mr-1.5 inline animate-spin" aria-hidden />}{hint}
    </div> : rows.map((row, index) => {
      const selected = index === menu.cursor
      const subtitle = row.kind === 'file' ? row.sub : `${row.kind} · ${row.sub}`
      return <ComposerPersonItem key={`${row.kind}:${row.id}`} person={{ name: row.name, role: row.kind }} active={selected}
        meta={<span className="flex min-w-0 items-center gap-2 text-xs text-on-surface-low"><span className="truncate">{subtitle}</span>
          {row.size !== undefined && <span className="shrink-0 tabular-nums">{formatMentionSize(row.size)}</span>}</span>}
        id={`${idPrefix}-opt-${index}`} role="option"
        aria-selected={selected} title={row.sub} onMouseEnter={() => menu.selectCursor(index)}
        onMouseDown={event => { event.preventDefault(); choose(index) }} className={composerOptionClass}
        style={selected ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined} />
    })}
  </div>, document.body)
}
