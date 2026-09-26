import { useMemo } from 'react'
import { motion } from 'framer-motion'
import { Bell, Check, CheckCheck, Trash2, Undo2, X, Target } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { SidePanel } from '../../shared/ui/SidePanel'
import { ListControls } from '../../shared/ui/ListControls'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { Markdown } from '../../shared/ui/Markdown'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { RowHitTarget } from '../../shared/ui/RowHitTarget'
import { UnreadRail } from './UnreadRail'
import { useNotificationFeed } from './notificationFeedState'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { spring } from '../../shared/theme/motion'
import { rowSubject } from '../../shared/data/rowSubject'
import { type NotificationItem } from '../../shared/data/api'
import { kindMeta, BUCKET_ORDER, relTime, clockTime, firstLine, toneChipBg } from './notificationMeta'
import { fvs } from '../../shared/theme/fontWeight'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { PageTitle } from '../../shared/ui/PageTitle'
import { accentChip, toneChipSkin } from '../../shared/theme/accent'

export function NotificationsPage({ query, setQuery, navigate }: Pick<RouteProps, 'query' | 'setQuery' | 'navigate'>) {
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'all', { replace: true })
  const [openTsRaw, setOpenTs] = useQueryParam(query, setQuery, 'open', '')
  const openTs = openTsRaw || null
  const { items, loadErr, now, filtered, groups, unread, kinds, counts, open, undoable,
    load, ack, unack, ackAll, remove, clearAll, undoAction } = useNotificationFeed(filter, openTs, setOpenTs)

  const filterSection: FilterSectionDef = useMemo(() => ({
    title: 'Show', value: filter, defaultKey: 'all', onChange: setFilter,
    options: [
      { key: 'all', label: 'All', count: items?.length ?? 0 },
      { key: 'unread', label: 'Unread', count: unread },
      ...kinds.map((k) => ({ key: k, label: kindMeta(k).label, count: counts.get(k) ?? 0 })),
    ],
  }), [items, unread, kinds, counts, filter, setFilter])

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle className="flex items-center gap-s">Notifications {unread > 0 && <span className="rounded-pill px-2 h-5 inline-flex items-center text-[0.75rem]" style={accentChip}>{unread}</span>}</PageTitle>}
          right={items && items.length > 0 ? (

            <HeaderActions>
              {unread > 0 && <HeaderControl icon={CheckCheck} label="Mark all read" priority="primary" onClick={ackAll} />}
              <HeaderControl icon={Trash2} label="Clear all" danger priority="low" onClick={clearAll} />
            </HeaderActions>
          ) : undefined}
        />
      }
      controls={(items === undefined || items.length > 0 || filter !== 'all')
        ? <ListControls results={{ count: (filtered ?? []).length, noun: 'notifications', active: filter !== 'all' }}>
            <FilterMenu sections={[filterSection]} />
          </ListControls>
        : undefined}
      panel={open && (
        <SidePanel key={open.ts} fillHeight storeKey="notif-panel-w" icon={(() => { const km = kindMeta(open.kind); return <km.icon size={18} style={{ color: km.tone }} /> })()} title={open.title} onClose={() => setOpenTs("")}>
          <div className="grid gap-l">
            <div className="flex flex-wrap items-center gap-s text-[0.8125rem]">
              {(() => { const km = kindMeta(open.kind); return <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={toneChipSkin(km.tone, 16)}><km.icon size={13} /> {km.label}</span> })()}
              <span className="text-on-surface-low">{clockTime(open.ts)}</span>
              {open.acked && <span className="text-on-surface-low inline-flex items-center gap-1"><Check size={13} /> read</span>}
            </div>
            <div className="text-on-surface-var text-[0.9375rem] leading-relaxed"><Markdown>{open.body}</Markdown></div>
            <div className="flex flex-wrap gap-s border-t border-outline-variant/40 pt-l">

              {open.loop_id && (
                <Button size="sm" onClick={() => { ack(open); navigate(`${open.loop_kind === 'code' ? 'code' : 'loops'}/${open.loop_id}`) }}>
                  <Target size={14} /> {open.loop_kind === 'code' ? 'Open project' : 'Open loop'}
                </Button>
              )}

              {open.reversal_id && undoable.has(open.reversal_id) && (
                <Button size="sm" variant="secondary" onClick={() => { undoAction(open); ack(open) }}>
                  <Undo2 size={14} /> Undo & stop doing this automatically
                </Button>
              )}
              {open.acked
                ? <Button size="sm" variant="ghost" onClick={() => unack(open)}><Undo2 size={14} /> Mark unread</Button>
                : <Button size="sm" variant="secondary" onClick={() => ack(open)}><Check size={14} /> Mark read</Button>}
              <Button size="sm" variant="ghost" onClick={() => remove(open)}><Trash2 size={14} /> Delete</Button>
            </div>
          </div>
        </SidePanel>
      )}
    >
      <div className="mx-auto px-l py-xl" style={{ maxWidth: 'var(--content-width)' }}>
        {items === undefined && loadErr ? (

          <LoadError what="notifications" error={loadErr} onRetry={load} />
        ) : filtered === null ? <ListSkeleton rows={6} what="notifications" /> : items && items.length === 0 && filter === 'all' ? (
          <EmptyState icon={Bell} title="You're all caught up" hint="Schedule runs, trigger fires, agent updates, and task results surface here for you to review." />
        ) : (
          <>
            {filtered.length === 0 ? (
              <EmptyState icon={Bell} title="No notifications match" hint="Try a different filter."
                action={{ label: 'Clear filter', onClick: () => setFilter('all') }} />
            ) : (
              <div className="grid gap-l">
                {BUCKET_ORDER.filter((b) => groups[b]?.length).map((b) => (
                  <div key={b}>
                    <div className="mb-m flex items-center gap-s border-b border-outline/30 pb-s text-on-surface-low text-[0.75rem] uppercase tracking-wide">{b}</div>
                    <div className="flex flex-col gap-s">
                      {groups[b].map((n, i) => <Row key={n.ts} n={n} index={i} now={now} onOpen={() => setOpenTs(n.ts)} onAck={() => ack(n)} onUnack={() => unack(n)} onDelete={() => remove(n)} />)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </WorkbenchLayout>
  )
}

function Row({ n, index, now, onOpen, onAck, onUnack, onDelete }: { n: NotificationItem; index: number; now: number; onOpen: () => void; onAck: () => void; onUnack: () => void; onDelete: () => void }) {
  const km = kindMeta(n.kind)

  const subject = rowSubject([n.title, firstLine(n.body ?? '')])

  const readAction = n.acked
    ? { icon: Undo2, label: 'Mark unread', onSelect: onUnack }
    : { icon: Check, label: 'Mark read', onSelect: onAck }
  const menuItems: ContextMenuItem[] = [
    { icon: <Bell size={15} />, label: 'Open', onSelect: onOpen },
    { icon: <readAction.icon size={15} />, label: readAction.label, onSelect: readAction.onSelect },
    { icon: <Trash2 size={15} />, label: 'Delete', onSelect: onDelete, danger: true },
  ]
  return (
    <ContextMenu items={menuItems}>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}

      tabIndex={-1}
      className="group relative flex items-center gap-m rounded-lg border border-outline/25 bg-surface-container px-m py-m cursor-pointer hover:bg-surface-high transition-colors has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary"
      onClick={onOpen}>

      <UnreadRail tone={km.tone} acked={n.acked} />
      <RowHitTarget label={subject} />
      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: toneChipBg(km.tone) }}><km.icon size={19} style={{ color: km.tone }} /></span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-s">
          <span className={`truncate text-[0.9375rem] ${n.acked ? 'text-on-surface-var' : 'text-on-surface'}`} style={fvs(500)}>{n.title}</span>
          <span className="shrink-0 text-on-surface-low text-[0.75rem]">{relTime(n.ts, now)}</span>
        </div>
        <p className="mt-0.5 truncate text-on-surface-low text-[0.8125rem]">{firstLine(n.body)}</p>
      </div>
      <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>

        <InvestigateButton kind="notification" id={n.ts} backLink="#/notifications" size={34}
          label={`Investigate in chat: ${subject}`} />
        <IconButton icon={readAction.icon} label={`${readAction.label}: ${subject}`} title={readAction.label}
          size={34} onClick={readAction.onSelect} />
        <IconButton icon={X} label={`Delete: ${subject}`} title="Delete" size={34} tone="danger" onClick={onDelete} />
      </div>
    </motion.div>
    </ContextMenu>
  )
}
