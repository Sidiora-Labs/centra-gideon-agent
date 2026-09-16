import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, GitMerge, Loader2, Pencil, Tag as TagIcon, Trash2 } from 'lucide-react'
import { api, ApiError, type KnowledgeTag } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { confirmDelete, confirmDestructive, promptInput } from '../../shared/ui/dialog'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { EmptyState, ListSkeleton } from '../../shared/ui/ListScaffold'
import { QuietButton } from '../../shared/ui/QuietButton'
import { fvs } from '../../shared/theme/fontWeight'

export function TagManager({ onChanged }: { onChanged?: () => void }) {
  const [tags, setTags] = useState<KnowledgeTag[] | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    let alive = true
    api.knowledgeTagTree()
      .then((t) => { if (alive) setTags(t) })
      .catch(() => { if (alive) setTags([]) })
    return () => { alive = false }
  }, [])

  const run = async (
    id: number,
    label: string,
    fn: () => Promise<{ tags?: KnowledgeTag[] } | void>,
  ) => {
    setBusy(id); setNote('')
    try {
      const r = await fn()
      const next = r && 'tags' in r && r.tags ? r.tags : await api.knowledgeTagTree()
      setTags(next)
      setNote(label)
      onChanged?.()
    } catch (e) {
      notify(e instanceof ApiError ? e.message : "Couldn't update the tag.", 'error')
    } finally {
      setBusy(null)
    }
  }

  const ordered = useMemo(() => {
    if (!tags) return []
    const roots = tags.filter((t) => t.parent_id === null)
    const kids = (pid: number) => tags.filter((t) => t.parent_id === pid)
    const out: { tag: KnowledgeTag; depth: number }[] = []
    for (const root of roots) {
      out.push({ tag: root, depth: 0 })
      for (const kid of kids(root.id)) out.push({ tag: kid, depth: 1 })
    }
    for (const t of tags) if (!out.some((o) => o.tag.id === t.id)) out.push({ tag: t, depth: 1 })
    return out
  }, [tags])

  if (tags === null) return <ListSkeleton what="tags" />
  if (!tags.length) {
    return (
      <EmptyState icon={TagIcon} title="No tags yet"
        hint="Tag a saved item and it appears here, where you can rename, nest, or merge it." />
    )
  }

  const rename = (t: KnowledgeTag) => run(t.id, `Renamed “${t.name}”`, async () => {
    const name = await promptInput({
      title: `Rename “${t.name}”`, label: 'Tag name', initial: t.name,
    })
    if (!name || name === t.name) return
    return api.renameKnowledgeTag(t.id, { name })
  })

  const reparent = (t: KnowledgeTag, parentId: number | null) =>
    run(t.id, parentId === null ? `“${t.name}” is now top-level` : `Moved “${t.name}”`,
      () => api.renameKnowledgeTag(t.id, { parent_id: parentId }))

  const merge = (t: KnowledgeTag, into: KnowledgeTag) =>
    run(t.id, `Merged “${t.name}” into “${into.name}”`, async () => {
      const kids = tags.filter((x) => x.parent_id === t.id)
      const ok = await confirmDestructive(
        `Merge “${t.name}” into “${into.name}”?`,
        [
          t.usage_count
            ? `This retags ${t.usage_count} item${t.usage_count === 1 ? '' : 's'} as “${into.name}”.`
            : `“${t.name}” is on no items.`,
          ` The tag “${t.name}” is then deleted from the taxonomy.`,
          kids.length
            ? ` Its ${kids.length} nested tag${kids.length === 1 ? '' : 's'} become top-level rather than being deleted.`
            : '',
          ' This cannot be undone.',
        ].join(''),
        { confirmLabel: 'Merge' },
      )
      if (!ok) return
      await api.mergeKnowledgeTag(t.id, into.id)
    })

  const remove = (t: KnowledgeTag) => run(t.id, `Deleted “${t.name}”`, async () => {
    const kids = tags.filter((x) => x.parent_id === t.id)
    const ok = await confirmDelete('tag', t.name, {
      body: [
        t.usage_count
          ? `This removes the tag from ${t.usage_count} item${t.usage_count === 1 ? '' : 's'}. The items themselves are untouched.`
          : 'This tag is on no items.',
        kids.length
          ? ` Its ${kids.length} nested tag${kids.length === 1 ? '' : 's'} become top-level rather than being deleted.`
          : '',
      ].join(''),
    })
    if (!ok) return
    await api.deleteKnowledgeTag(t.id)
  })

  return (
    <div className="flex flex-col gap-1">
      {
}
      <span role="status" aria-live="polite" className="sr-only">{note}</span>
      {note && (
        <div aria-hidden="true" data-type="body-s" className="pb-1 text-on-surface-var">{note}</div>
      )}
      {ordered.map(({ tag, depth }) => {
        const others = tags.filter((x) => x.id !== tag.id)
        const menu: ContextMenuItem[] = [
          { icon: <Pencil size={15} />, label: 'Rename', onSelect: () => rename(tag) },
          ...(tag.parent_id !== null
            ? [{ icon: <TagIcon size={15} />, label: 'Make top-level', onSelect: () => reparent(tag, null) }]
            : []),
          ...others
            .filter((o) => o.parent_id !== tag.id && o.id !== tag.parent_id)
            .slice(0, 8)
            .map((o) => ({
              icon: <ChevronRight size={15} />,
              label: `Nest under ${o.name}`,
              onSelect: () => reparent(tag, o.id),
            })),
          ...others.slice(0, 8).map((o) => ({
            icon: <GitMerge size={15} />,
            label: `Merge into ${o.name}`,
            onSelect: () => merge(tag, o),
          })),
          { icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => remove(tag) },
        ]
        return (
          <ContextMenu key={tag.id} items={menu}>
            <div className="group flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2"
              style={{ marginLeft: depth * 20 }}>
              {depth > 0 && <ChevronRight size={12} className="shrink-0 text-on-surface-low" aria-hidden />}
              <TagIcon size={14} className="shrink-0 text-on-surface-low" aria-hidden />
              {
}
              <span data-type="body-m" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(500)} title={tag.name}>
                {tag.name}
              </span>
              <span data-type="caption" className="shrink-0 text-on-surface-low">
                {tag.usage_count === 0
                  ? 'unused'
                  : `${tag.usage_count} item${tag.usage_count === 1 ? '' : 's'}`}
              </span>
              {busy === tag.id
                ? <Loader2 size={13} className="shrink-0 animate-spin text-on-surface-low" />
                : (
                  <span className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <QuietButton onClick={() => rename(tag)} title={`Rename ${tag.name}`}>
                      <Pencil size={12} /> Rename
                    </QuietButton>
                  </span>
                )}
            </div>
          </ContextMenu>
        )
      })}
      <p data-type="caption" className="pt-1 text-on-surface-low">
        {
}
        Right-click a tag to nest, merge, or delete it, or Tab to one and press Shift+F10 for
        the same menu. An unused tag is kept — it stays part of your taxonomy even when nothing
        carries it right now.
      </p>
    </div>
  )
}
