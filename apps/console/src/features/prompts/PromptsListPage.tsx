// A separator tests PRESENCE, not line position; wrapping metadata uses gaps.
import { useMemo, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Plus, FileText, Puzzle, Maximize2, User, Cog } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../shared/ui/HeaderActions'
import { ListControls } from '../../shared/ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { SidePanel } from '../../shared/ui/SidePanel'
import { ContextMenu, type ContextMenuItem, Disintegrate } from '../../shared/ui/motion'
import { usePromptLibrary, selectPromptRows } from './promptLibraryState'
import { type PromptItem, type PromptSnippet } from '../../shared/data/api'
import { promptVars, sourceTone, sourceLabel } from './promptMeta'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/shell/useQueryState'
import { PageTitle } from '../../shared/ui/PageTitle'

type Tab = 'system' | 'user' | 'snippets'
const TABS: { key: Tab; label: string; icon: typeof User }[] = [
  { key: 'user', label: 'User', icon: User },
  { key: 'system', label: 'System', icon: Cog },
  { key: 'snippets', label: 'Snippets', icon: Puzzle },
]

type SortKey = 'name' | 'updated' | 'source' | 'vars'
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'name', label: 'Name (A–Z)' },
  { key: 'updated', label: 'Recently updated' },
  { key: 'source', label: 'Source' },
  { key: 'vars', label: 'Variable count' },
]
const SOURCES = [
  { key: 'all', label: 'All sources' },
  { key: 'user', label: 'User' },
  { key: 'bundled', label: 'Bundled' },
  { key: 'marketplace', label: 'Marketplace' },
]
type Row = PromptItem & PromptSnippet

function OpenFullPageBar({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen}
      data-type="body-s"
      className="mb-l inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1 text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface">
      <Maximize2 size={13} /> Open full page
    </button>
  )
}

export function PromptsListPage({ onCreate, onOpen, navigate, query, setQuery }: {
  onCreate: (tab: Tab) => void
  onOpen: (tab: Tab, name: string, opts?: { edit?: boolean }) => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const { items, snippets, itemsErr, snipsErr, load } = usePromptLibrary()
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [tabRaw, setTabRaw] = useQueryParam(query, setQuery, 'tab', 'user', { replace: true })
  const tab = (TABS.some((t) => t.key === tabRaw) ? tabRaw : 'user') as Tab
  const setTab = (t: Tab) => setTabRaw(t)
  const [openNameRaw, setOpenName] = useQueryParam(query, setQuery, 'open', '')
  const openName = openNameRaw || null
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const [sortRaw, setSort] = useQueryParam(query, setQuery, 'sort', 'name', { replace: true })
  const sort = (SORTS.some((s) => s.key === sortRaw) ? sortRaw : 'name') as SortKey
  const [source, setSource] = useQueryParam(query, setQuery, 'src', 'all', { replace: true })

  const isSnips = tab === 'snippets'
  const [deletingName, setDeletingName] = useState<string | null>(null)
  const onDeleted = (name: string) => { setOpenName(''); setDeletingName(name) }
  const finishDelete = () => { setDeletingName(null); load() }
  const rows = useMemo<Row[] | null>(() => {
    if (isSnips) return snippets ? selectPromptRows(snippets as Row[], q, sort, source) : null
    if (!items) return null
    const byKind = (items as Row[]).filter((p) => (p.kind ?? 'user') === tab)
    return selectPromptRows(byKind, q, sort, source)
  }, [isSnips, items, snippets, q, sort, source, tab])

  const openPrompt = items?.find((p) => p.name === openName) ?? null
  const openSnip = snippets?.find((s) => s.name === openName) ?? null
  const loading = rows === null
  const count = rows?.length ?? 0
  const loadErr = isSnips ? snipsErr : itemsErr
  const anyItems = isSnips ? (snippets === undefined || (snippets?.length ?? 0) > 0) : (items === undefined || (items?.length ?? 0) > 0)

  const filterSections: FilterSectionDef[] = [
    { title: 'Sort by', value: sort, defaultKey: 'name', onChange: (k) => setSort(k), options: SORTS.map((s) => ({ key: s.key, label: s.label })) },
    { title: 'Source', value: source, defaultKey: 'all', onChange: (k) => setSource(k), options: SOURCES.map((s) => ({ key: s.key, label: s.label })) },
  ]

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Prompts</PageTitle>}
          right={
            <HeaderActions>
              <HeaderSegmented ariaLabel="Prompt kind" value={tab} onChange={(v) => { setTab(v as Tab); setQuery({ open: null, edit: null }) }} options={TABS} />
              <HeaderControl icon={Plus} label={isSnips ? 'New snippet' : 'New prompt'} variant="primary" priority="primary" onClick={() => onCreate(tab)} />
            </HeaderActions>
          }
        />
      }
      controls={anyItems
        ? <ListControls
            search={{ value: q, onChange: setQ, placeholder: isSnips ? 'Search snippets' : 'Search prompts', label: 'Search' }}
            results={{ count: (rows ?? []).length, noun: isSnips ? 'snippets' : 'prompts', active: !!q.trim() }}>
            <FilterMenu sections={filterSections} label="Sort & filter" />
          </ListControls>
        : undefined}
      panel={
        isSnips
          ? (openSnip && (
              <SidePanel key={openSnip.name} fillHeight storeKey="prompt-panel-w" icon={<Puzzle size={18} style={{ color: sourceTone(openSnip.source) }} />} title={openSnip.name} onClose={() => setQuery({ open: null, edit: null })}>
                <OpenFullPageBar onOpen={() => onOpen('snippets', openSnip.name)} />
                <SnippetDetail snippet={openSnip} editing={editing} onEditingChange={setEditing} onSaved={() => load()} onDeleted={() => onDeleted(openSnip.name)} />
              </SidePanel>
            ))
          : (openPrompt && (
              <SidePanel key={openPrompt.name} fillHeight storeKey="prompt-panel-w" icon={<FileText size={18} style={{ color: sourceTone(openPrompt.source) }} />} title={openPrompt.name} onClose={() => setQuery({ open: null, edit: null })}>
                <OpenFullPageBar onOpen={() => onOpen((openPrompt.kind ?? 'user') as Tab, openPrompt.name)} />
                <PromptDetail prompt={openPrompt} editing={editing} onEditingChange={setEditing} onSaved={() => load()} onDeleted={() => onDeleted(openPrompt.name)} onNavigate={navigate} />
              </SidePanel>
            ))
      }
    >
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {rows === null && loadErr ? (
          <LoadError what={isSnips ? 'snippets' : 'prompts'} error={loadErr} onRetry={load} />
        ) : loading ? <ListSkeleton rows={6} what={isSnips ? 'snippets' : 'prompts'} /> : count === 0 ? (
          isSnips ? (
            <EmptyState icon={Puzzle} title={q ? 'No matching snippets' : 'No snippets'} hint={q ? 'Try a different term.' : 'Snippets are reusable fragments other prompts include with {{> name}}. Their variables merge into the including prompt.'} action={!q ? { label: 'New snippet', onClick: () => onCreate('snippets'), icon: Plus } : undefined} />
          ) : (
            <EmptyState icon={FileText} title={q ? 'No matching prompts' : `No ${tab} prompts`} hint={q ? 'Try a different term.' : tab === 'system' ? 'System prompts are bound to a use-case (chat / background / code / goal loop) and injected as the agent system prompt.' : 'User prompts are invoked in chat with filled-in {{variables}}.'} action={!q ? { label: 'New prompt', onClick: () => onCreate(tab), icon: Plus } : undefined} />
          )
        ) : (
          <div className="grid gap-s">
            {rows!.map((r, i) => {
              const Icon = isSnips ? Puzzle : FileText
              const vars = promptVars(r)
              const menuItems: ContextMenuItem[] = [
                { icon: <Icon size={15} />, label: 'Open', onSelect: () => setQuery({ open: r.name, edit: null }) },
                { icon: <Maximize2 size={15} />, label: 'Open full page', onSelect: () => onOpen(isSnips ? 'snippets' : ((r.kind ?? 'user') as Tab), r.name) },
              ]
              return (
                <Disintegrate key={r.name} active={deletingName === r.name} onDone={finishDelete}>
                <ContextMenu items={menuItems}>
                <ListRow index={i} accent={sourceTone(r.source)} onClick={() => setQuery({ open: r.name, edit: null })} label={r.name}>
                  <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${sourceTone(r.source)} 16%, transparent)` }}><Icon size={19} style={{ color: sourceTone(r.source) }} /></span>
                  <div className="flex-1 min-w-0">
                    <span data-type="label-m" className="truncate text-on-surface font-mono" style={fvs(500)}>{r.name}</span>
                    <div data-type="body-s" className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low">
                      <span>{sourceLabel(r.source, r.tags)}</span>
                      {vars.length > 0 && <span>{vars.length} var{vars.length > 1 ? 's' : ''}</span>}

                      {r.description && <span className="truncate">{r.description}</span>}
                    </div>
                  </div>
                  {(r.tags?.length ?? 0) > 0 && <div className="hidden md:flex shrink-0 gap-xs">{r.tags!.slice(0, 2).map((t) => <span key={t} data-type="caption" className="rounded-md bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var">{t}</span>)}</div>}
                </ListRow>
                </ContextMenu>
                </Disintegrate>
              )
            })}
          </div>
        )}
      </div>
    </WorkbenchLayout>
  )
}
