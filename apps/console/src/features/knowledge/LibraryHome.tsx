import { useMemo, useState } from 'react'
import { BookOpen, Check, Clock, Layers, Star } from 'lucide-react'
import { ProgressRing } from '../../shared/ui/ProgressRing'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { InlineError } from '../../shared/ui/InlineError'
import { ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, type KnowledgeItem, type KnowledgeLibraryHome } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { relTime, typeLabel } from './knowledgeMeta'
import { clearReadingPosition, readingPositions } from './readingPosition'

export function LibraryHome({ onOpenItem, onOpenReader, onOpenCollection, onShowCuration }: {
  onOpenItem: (id: string) => void
  onOpenReader: (id: string) => void
  onOpenCollection: (id: string) => void
  onShowCuration: (filter: 'favorites' | 'reading') => void
}) {
  const { data, error, loading, refresh } = useQuery<KnowledgeLibraryHome>(
    'knowledge:library-home', () => api.knowledgeLibraryHome())
  const [writeErr, setWriteErr] = useState('')

  const positions = useMemo(() => readingPositions(), [data])

  if (loading && !data) return <ListSkeleton rows={5} what="library" />
  if (!data) return <LoadError what="library" error={error} onRetry={refresh} />

  const reading = [...data.continue_reading].sort((a, b) =>
    (positions[b.id]?.ts ?? 0) - (positions[a.id]?.ts ?? 0))

  const markRead = async (it: KnowledgeItem) => {
    const name = it.title || it.url_title || 'that item'
    setWriteErr('')
    try {
      await api.setKnowledgeReadState(it.id, 'read')
    } catch {
      setWriteErr(`Couldn't mark “${name}” read — it is still where you left it.`)
      return
    }
    clearReadingPosition(it.id)
    invalidateKeys('knowledge:items', true)
    refresh()
  }

  return (
    <div className="flex min-w-0 flex-col gap-2xl">
      {
}
      {error ? (
        <InlineError icon onRetry={refresh}>Couldn't refresh your library — showing the last copy.</InlineError>
      ) : null}
      {writeErr ? <InlineError icon onDismiss={() => setWriteErr('')}>{writeErr}</InlineError> : null}

      <Shelf id="home-collections" icon={Layers} title="Shelves" count={data.collections.length}
        empty="No shelves yet — group items into one from the Library lens's shelf rail.">
        <ul className="flex flex-wrap gap-s">
          {data.collections.map((c) => (
            <li key={c.id}>
              {
}
              <Button variant="secondary" size="xs" onClick={() => onOpenCollection(c.id)}
                ariaLabel={`${c.name || 'Untitled shelf'}, ${c.count}${c.count_capped ? ' or more' : ''} item${c.count === 1 && !c.count_capped ? '' : 's'}`}>
                {c.name || 'Untitled shelf'}
                <span className="text-on-surface-low tabular-nums">{c.count}{c.count_capped ? '+' : ''}</span>
              </Button>
            </li>
          ))}
        </ul>
      </Shelf>

      <Shelf id="home-reading" icon={BookOpen} title="Continue reading" count={reading.length}
        empty="Nothing in progress. Open something and it will wait here at the paragraph you stopped on."
        action={reading.length > 0 ? { label: 'View all in progress', onClick: () => onShowCuration('reading') } : undefined}>
        <ItemRows items={reading} onOpen={onOpenItem} subtitle={(it) => {
          const pos = positions[it.id]
          return pos ? `${Math.round(pos.pct * 100)}% in · ${typeLabel(it)}` : `Not started · ${typeLabel(it)}`
        }} lead={(it) => (
          <ProgressRing pct={positions[it.id]?.pct ?? 0} tone="var(--color-primary)" size={26}
            label={`Reading progress on ${it.title || it.url_title || 'untitled item'}: ${Math.round((positions[it.id]?.pct ?? 0) * 100)}%`} />
        )} actions={(it) => (
          <>
            <IconButton icon={BookOpen} size={32} iconSize={16}
              label={`Resume reading: ${it.title || it.url_title || 'untitled item'}`} title="Resume reading"
              onClick={(e) => { e.stopPropagation(); onOpenReader(it.id) }} />
            <IconButton icon={Check} size={32} iconSize={16}
              label={`Mark read: ${it.title || it.url_title || 'untitled item'}`} title="Mark read"
              onClick={(e) => { e.stopPropagation(); void markRead(it) }} />
          </>
        )} />
      </Shelf>

      <Shelf id="home-recent" icon={Clock} title="Recently added" count={data.recently_added.length}
        empty="Nothing added yet — anything you add, upload or let a watched source pull in lands here first.">
        <ItemRows items={data.recently_added} onOpen={onOpenItem}
          subtitle={(it) => [typeLabel(it), relTime(it.created_at)].filter(Boolean).join(' · ')} />
      </Shelf>

      <Shelf id="home-favorites" icon={Star} title="Favorites" count={data.favorites.length}
        empty="No favorites yet — star an item and it lands here."
        action={data.favorites.length > 0 ? { label: 'View all favorites', onClick: () => onShowCuration('favorites') } : undefined}>
        <ItemRows items={data.favorites} onOpen={onOpenItem}
          subtitle={(it) => [typeLabel(it), relTime(it.updated_at)].filter(Boolean).join(' · ')} />
      </Shelf>
    </div>
  )
}

function Shelf({ id, icon: Icon, title, count, empty, action, children }: {
  id: string
  icon: typeof Star
  title: string
  count: number
  empty: string
  action?: { label: string; onClick: () => void }
  children?: React.ReactNode
}) {
  return (
    <section aria-labelledby={`${id}-heading`} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <Icon size={15} className="text-primary shrink-0" aria-hidden />
        {
}
        <h2 id={`${id}-heading`} data-type="label-l" className="text-on-surface-var">{title}</h2>
        <span data-type="label-s" className="text-on-surface-low tabular-nums">{count}</span>
        <span className="h-px flex-1 bg-outline-variant/40" />
        {action && (
          <Button variant="ghost-accent" size="xs" onClick={action.onClick}>{action.label}</Button>
        )}
      </div>
      {count === 0 ? <p data-type="body-s" className="text-on-surface-low">{empty}</p> : children}
    </section>
  )
}

function ItemRows({ items, onOpen, subtitle, lead, actions }: {
  items: KnowledgeItem[]
  onOpen: (id: string) => void
  subtitle: (it: KnowledgeItem) => string
  lead?: (it: KnowledgeItem) => React.ReactNode
  actions?: (it: KnowledgeItem) => React.ReactNode
}) {
  return (
    <ul className="flex min-w-0 flex-col gap-s">
      {items.map((it, i) => {
        const name = it.title || it.url_title || '(untitled)'
        return (
          <li key={it.id} className="min-w-0">
            <ListRow index={i} label={name} onClick={() => onOpen(it.id)}>
              {lead?.(it)}
              <div className="min-w-0 flex-1">
                <p className="truncate text-on-surface" data-type="label-m">{name}</p>
                <p className="truncate text-on-surface-low" data-type="body-s">{subtitle(it)}</p>
              </div>
              {actions && <div className="flex shrink-0 items-center gap-1">{actions(it)}</div>}
            </ListRow>
          </li>
        )
      })}
    </ul>
  )
}
