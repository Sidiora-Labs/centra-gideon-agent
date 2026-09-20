import { useMemo } from 'react'
import { useInboxQueue, projectInbox } from './inboxQueueState'
import { fvs } from '../../shared/theme/fontWeight'
import { Inbox as InboxIcon, CheckCheck, RotateCcw, Circle, Reply, Settings as SettingsIcon, ScrollText, Loader2, ExternalLink, LayoutGrid, StickyNote, Star, Users, UserRound } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { WindowedList } from '../../shared/ui/WindowedList'
import { SidePanel } from '../../shared/ui/SidePanel'
import { ListControls } from '../../shared/ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { Popover, MenuRow } from '../../shared/ui/Popover'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { useQueryParam, useQueryFlag, type RouteProps } from '../../app/shell/useQueryState'
import type { InboxItem } from '../../shared/data/api'
import { rowSubject } from '../../shared/data/rowSubject'
import { previewText } from '../../shared/data/previewText'
import { Segmented } from '../../shared/ui/Segmented'
import { classMeta, confMeta, statusMeta, kindMeta, channelLabel, relPast, isOpen, NON_CHANNEL_ITEM_KINDS, refTarget, refLabel } from './inboxMeta'
import { InboxDetail } from './InboxDetail'
import { InboxSettingsPanel } from './InboxSettingsPanel'
import { ComposeNoteModal } from './ComposeNoteModal'
import { ProposalsLens } from './ProposalsLens'
import { TriageDigestCard } from './TriageDigestCard'
import { ContextMenu, EntranceGroup, EntranceRegion, type ContextMenuItem } from '../../shared/ui/motion'
import { PageTitle } from '../../shared/ui/PageTitle'

const FILTERS = [
  { key: 'open', label: 'Open' },
  { key: 'needs_reply', label: 'Needs reply' },
  { key: 'all', label: 'All' },
  { key: 'handled', label: 'Done' },
]

export function InboxPage({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'open', { replace: true })
  const [kind, setKind] = useQueryParam(query, setQuery, 'kind', '', { replace: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [ownership, setOwnership] = useQueryParam(query, setQuery, 'owner', 'everyone', { replace: true })
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null
  const [settingsOpen, setSettingsOpen] = useQueryFlag(query, setQuery, 'settings')

  const [captureOpen, setCaptureOpen] = useQueryFlag(query, setQuery, 'capture')
  const { items, itemsErr, itemsStale, status, open, load, reload, watched, busy, dismissAll, restart, digest } = useInboxQueue(openId, setOpenId)
  const currentOwner = status?.owner ?? ''
  const belongsToOwner = (item: InboxItem) => !currentOwner || !item.owner || item.owner.trim().toLowerCase() === currentOwner.trim().toLowerCase()
  const foreignCount = currentOwner ? (items ?? []).filter(item => !belongsToOwner(item)).length : 0
  const visibleItems = ownership === 'mine' ? items?.filter(belongsToOwner) : items
  const { filtered, filterCount, kindChips } = useMemo(() => projectInbox(visibleItems, filter, kind, q), [visibleItems, filter, kind, q])

  const health = status?.health
  const disabled = status ? !status.enabled : false

  const narrowed = !!(q.trim() || filter !== 'open' || kind)

  const filterSections: FilterSectionDef[] = [{
    title: 'Show', value: filter, defaultKey: 'open', onChange: setFilter,

    options: [
      ...FILTERS.map((f) => ({ key: f.key, label: f.label, count: filterCount(f.key) })),

      ...((filterCount('favorites') ?? 0) > 0 ? [{ key: 'favorites', label: 'Favorites', count: filterCount('favorites') }] : []),
      ...((filterCount('filtered') ?? 0) > 0 ? [{ key: 'filtered', label: 'Filtered', count: filterCount('filtered') }] : []),
    ],
  }, ...(foreignCount > 0 ? [{
    title: 'Owner', value: ownership, defaultKey: 'everyone', onChange: setOwnership,
    options: [
      { key: 'everyone', label: 'Everyone', icon: Users, count: items?.length },
      { key: 'mine', label: 'Mine', icon: UserRound, count: (items?.length ?? 0) - foreignCount },
    ],
  }] : [])]
  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding

          left={<PageTitle className="flex min-w-0 items-baseline gap-s"><span className="shrink-0">Inbox</span> {status && <span data-type="caption" className="min-w-0 truncate text-on-surface-low">{status.open_count} open · {status.total_count} total</span>}</PageTitle>}
          right={

            <div className="flex items-center gap-1">

              {watched.length > 0 && (
                <Popover placement="bottom" align="right" trigger={(open, toggle) => (
                  <button onClick={toggle} disabled={busy}
                    data-type="body-s"
                    className="inline-flex items-center gap-1.5 rounded-pill h-9 px-m text-on-surface-var hover:bg-surface-high hover:text-on-surface transition-colors disabled:opacity-40"
                    style={{ background: open ? 'var(--color-surface-high)' : undefined }}
                    title="Generate a catch-up digest for a watched channel">
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <ScrollText size={15} />}
                    <span className="hidden sm:inline">Digest</span>
                  </button>
                )}>
                  {(close) => (
                    <div className="flex flex-col gap-0.5" style={{ minWidth: 220 }}>
                      <div data-type="caption" className="px-m pt-1 pb-1.5 uppercase tracking-wide text-on-surface-low">Catch-up digest · last 4h</div>
                      {watched.map((ch) => (
                        <MenuRow key={ch.id} icon={<ScrollText size={15} />} label={ch.name || ch.id}
                          onClick={() => { close(); digest(ch.id) }} />
                      ))}
                    </div>
                  )}
                </Popover>
              )}
              <HeaderActions>
                {(status?.open_count ?? 0) > 0 && (
                  <HeaderControl icon={CheckCheck} label="Dismiss all" danger priority="low" onClick={dismissAll} disabled={busy} />
                )}
                <HeaderControl icon={RotateCcw} label="Restart sources" priority="low" onClick={restart} disabled={busy} />

                <HeaderControl icon={StickyNote} label="Capture a note" priority="primary" onClick={() => setCaptureOpen(true)} />
                <HeaderControl icon={SettingsIcon} label="Inbox settings" active={settingsOpen} priority="low" onClick={() => setSettingsOpen(!settingsOpen)} />
              </HeaderActions>
            </div>
          }
        />
      }
      controls={
        <ListControls search={(items === undefined || items.length > 0)
          ? { value: q, onChange: setQ, placeholder: 'Search inbox', label: 'Search inbox' }
          : undefined}

          results={{ count: (filtered ?? []).length, noun: 'items', active: narrowed }}
          stale={itemsStale}>
          <FilterMenu sections={filterSections} label="Show" />
        </ListControls>
      }
      panel={
        <>
          {open && (

            <SidePanel key={open.id} fillHeight storeKey="inbox-panel-w" urlKey={{ key: 'open', setQuery }}
              icon={(() => {
                const channelBacked = !NON_CHANNEL_ITEM_KINDS.includes(open.item_kind || 'message')
                const m = channelBacked ? classMeta(open.classification) : kindMeta(open.item_kind)
                return <m.icon size={18} style={{ color: m.tone }} />
              })()}
              title={!NON_CHANNEL_ITEM_KINDS.includes(open.item_kind || 'message')
                ? (open.sender_name || open.sender_id || 'Item')
                : kindMeta(open.item_kind).label}
              onClose={() => setOpenId("")}>
              <InboxDetail item={open} onChanged={load} navigate={navigate} />
            </SidePanel>
          )}
          {settingsOpen && (
            <SidePanel key="inbox-settings" fillHeight storeKey="inbox-panel-w" urlKey={{ key: 'settings', setQuery }} icon={<SettingsIcon size={18} className="text-primary" />} title="Inbox settings" onClose={() => setSettingsOpen(false)}>
              <InboxSettingsPanel />
            </SidePanel>
          )}
        </>
      }
    >

      <EntranceGroup>

        {status && (() => {
          const pollActive = (status.sources ?? []).filter((s) => s.kind === 'poll' && s.active)
          const hasPollProviders = (status.sources ?? []).some((s) => s.kind === 'poll')
          return (
            <EntranceRegion className="mx-auto w-full px-l" style={{ maxWidth: 'var(--content-width)' }}>
              <div data-type="body-s" className="flex items-center gap-m rounded-xl border border-outline/25 px-m py-m" style={{ background: 'var(--color-surface-container)' }}>
                <span className="relative flex size-2">
                  <span className="relative inline-flex size-2 rounded-pill" style={{ background: 'var(--color-ok)' }} />
                </span>
                <span className="text-on-surface-var">
                  Native source active — agents post here directly.
                  {pollActive.length > 0
                    ? ` Also polling ${pollActive.map((s) => s.name).join(', ')}${health?.last_poll_at ? ` · last checked ${relPast(health.last_poll_at)}` : ''}.`
                    : hasPollProviders ? ' Connect a message source (filesystem/Slack) to collect more.' : ''}
                </span>
              </div>
            </EntranceRegion>
          )
        })()}

        <EntranceRegion>
        <div data-tour="inbox" className="mx-auto px-l py-xl" style={{ maxWidth: 'var(--content-width)' }}>

        <TriageDigestCard />

        {kindChips.length > 1 && (
          <div className="mb-m">
            <Segmented
              size="sm"
              collapse="scroll"
              ariaLabel="Filter by kind"
              value={kind || 'all'}
              onChange={(k) => setKind(k === 'all' ? '' : k)}
              options={[
                { key: 'all', label: 'All', icon: LayoutGrid },
                ...kindChips.map((k) => ({
                  key: k.key,
                  label: k.open > 0 ? `${k.label} ${k.open}` : k.label,
                  icon: k.icon,
                  tone: k.tone,
                  title: `${k.label} — ${k.open} open`,
                })),
              ]}
            />
          </div>
        )}

        {filtered !== null && kind === 'proposal' ? (
          <ProposalsLens items={filtered} onChanged={reload} />
        ) : items === undefined && itemsErr ? (
          <LoadError what="inbox items" error={itemsErr} onRetry={reload} />
        ) : filtered === null ? <ListSkeleton rows={6} what="inbox items" /> : filtered.length === 0 ? (

          <EmptyState icon={InboxIcon}
            title={narrowed ? 'Nothing here' : disabled ? 'Inbox is not connected yet' : 'Inbox zero'}
            hint={narrowed
              ? (kind ? `No ${kindMeta(kind).label.toLowerCase()} matches the current search or filter.` : 'Try a different search or filter.')
              : disabled
                ? 'Inbox collects messages, questions, and notifications from your agents and connected sources (filesystem and Slack; email coming). Enable a source to begin.'
                : 'Messages your agents and connected sources surface for triage land here. You’re all caught up.'}
            action={disabled && !narrowed
              ? { label: 'Connect a source', onClick: () => navigate('settings/inbox'), icon: SettingsIcon }
              : undefined} />
        ) : (

          <WindowedList
            items={filtered}
            rowKey={(it) => it.id}

            rowHeights="variable"
            estimateRowHeight={76}
            gap={8}
            noun="items"
            findHint="use the Search inbox field above, which searches every item."
            anchorKey={openId ?? undefined}
            className="flex flex-col gap-s"
          >
            {(item, index, list) => <InboxQueueRow item={item} index={list.windowed ? 0 : index} owner={currentOwner}
              onOpen={() => setOpenId(item.id)} navigate={navigate} />}

          </WindowedList>
        )}
        </div>
        </EntranceRegion>
      </EntranceGroup>

      {captureOpen && (
        <ComposeNoteModal
          onClose={() => setCaptureOpen(false)}
          onCreated={() => { setCaptureOpen(false); load() }}
        />
      )}
    </WorkbenchLayout>
  )
}

function InboxQueueRow({ item, index, onOpen, navigate, owner }: {
  item: InboxItem; index: number; onOpen: () => void; navigate: (path: string) => void; owner: string
}) {
  const channel = !NON_CHANNEL_ITEM_KINDS.includes(item.item_kind || 'message')
  const kind = kindMeta(item.item_kind)
  const classification = classMeta(item.classification)
  const confidence = confMeta(item.confidence)
  const status = statusMeta(item.status)
  const visual = channel ? classification : kind
  const title = channel ? item.sender_name || item.sender_id || 'Unknown' : kind.label
  const target = refTarget(item)
  const pending = item.status === 'pending'
  const unresolved = isOpen(item.status)
  const preview = previewText(item.message)
  const attributedOwner = item.owner?.trim()
  const ownerLabel = attributedOwner && owner && attributedOwner.toLowerCase() === owner.trim().toLowerCase() ? 'you' : attributedOwner
  const context: ContextMenuItem[] = [
    { icon: <InboxIcon size={15} />, label: 'Open', onSelect: onOpen },
    ...(target ? [{ icon: <ExternalLink size={15} />, label: refLabel(item), onSelect: () => navigate(target) }] : []),
  ]
  return <ContextMenu items={context}>
    <ListRow index={index} accent={pending ? visual.tone : undefined} onClick={onOpen}
      label={rowSubject([title, preview])}>
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl"
        style={{ background: `color-mix(in srgb, ${visual.tone} 16%, transparent)` }}>
        <visual.icon size={18} style={{ color: visual.tone }} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-s">
          <span data-type="label-m" className={`truncate ${unresolved ? 'text-on-surface' : 'text-on-surface-var'}`} style={fvs(500)}>{title}</span>
          {channel && channelLabel(item) && <span data-type="caption" className="shrink-0 text-on-surface-low">{channelLabel(item)}</span>}
          {!channel && target && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low"><ExternalLink size={11} /> deep link</span>}
          {item.draft && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-ok"><Reply size={11} /> draft</span>}
          {item.favorited && <Star size={12} className="shrink-0 text-primary" style={{ fill: 'currentColor' }} aria-label="Favorited" />}
          {ownerLabel && <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low" title={`Owned by ${ownerLabel}`}><UserRound size={11} /> {ownerLabel}</span>}
        </div>
        <p data-type="body-s" className="mt-1 truncate text-on-surface-low">{preview}</p>
      </div>
      <div className="hidden shrink-0 items-center gap-m sm:flex">
        {channel && <span data-type="caption" title={confidence.label} style={{ color: confidence.tone }}><confidence.icon size={12} /></span>}
        {!unresolved ? <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low"><status.icon size={12} style={{ color: status.tone }} /> {status.label}</span>
          : item.created_at && <span data-type="caption" className="text-on-surface-low">{relPast(item.created_at)}</span>}
        {pending && <Circle size={7} fill={visual.tone} stroke="none" />}
      </div>
    </ListRow>
  </ContextMenu>
}
