import { useEffect, useMemo, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Plus, Zap, Clock, Pencil, CalendarDays, Users, ShieldOff, Trash2 } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { PresetEmptyState } from '../../shared/ui/PresetEmptyState'
import { Button } from '../../shared/ui/Button'
import { TRIGGER_PRESETS } from './triggerPresets'
import { SidePanel } from '../../shared/ui/SidePanel'
import { ListControls } from '../../shared/ui/ListControls'
import { Segmented } from '../../shared/ui/Segmented'
import { WeekGridView } from './WeekGridView'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { confirmDelete } from '../../shared/ui/dialog'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { useQueryParam, useEditFlag, type RouteProps } from '../../app/shell/useQueryState'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type ActionProvider } from '../../shared/data/api'
import { ScheduleDetail } from '../schedule/ScheduleDetail'
import { LifecycleDetail } from './LifecycleDetail'
import { StoreTriggerDetail } from './StoreTriggerDetail'
import { scheduleToTrigger, hookToTrigger, storeToTrigger, eventToTrigger, eventPatternMeta, relPast, useTriggerVariables, eventIsDormant, eventIsAgentScoped, type Trigger } from './triggerMeta'
import { RungChip } from '../../shared/ui/RungChip'
import { providerRungIndex, useAutonomyLadder } from '../../shared/data/rungs'
import { statusMeta, triggerHealthMeta, lastRunMeta, relFuture } from '../schedule/scheduleMeta'
import { PageTitle } from '../../shared/ui/PageTitle'
import { BUSY_REASON } from '../../shared/ui/unavailable'

const FILTERS: Array<{ key: string; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'schedule', label: 'Schedules' },
  { key: 'lifecycle', label: 'Lifecycle events' },
  { key: 'event', label: 'Data events' },
  { key: 'store', label: 'Automations' },
]


export function TriggersListPage({ onCreate, query, setQuery }: {
  onCreate: (presetId?: string) => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const [filter, setFilter] = useQueryParam(query, setQuery, 'filter', 'all', { replace: true })
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [openIdRaw, setOpenId] = useQueryParam(query, setQuery, 'open', '')
  const openId = openIdRaw || null
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const [view, setView] = useQueryParam(query, setQuery, 'view', 'list', { replace: true })

  const { data: schedules, error: schedulesErr, refresh: refreshSchedules } = useQuery('triggers:schedules', () => api.schedules().then((d) => d.jobs), { persist: false })
  const { data: hooks, error: hooksErr, refresh: refreshHooks } = useQuery('triggers:hooks', () => api.hooks(), { persist: true })
  const catalog = useTriggerVariables()
  const { data: stores, error: storesErr, refresh: refreshStores } = useQuery('triggers:store', () => api.storeTriggers(), { persist: false })
  const { data: events, error: eventsErr, refresh: refreshEvents } = useQuery('triggers:events', () => api.eventTriggers(), { persist: false })
  const { data: providers = [] } = useQuery('triggers:action-providers', () => api.actionProviders().catch(() => [] as ActionProvider[]), { persist: true })
  const { ladder } = useAutonomyLadder()
  const rungByProvider = useMemo(() => providerRungIndex(ladder), [ladder])

  const loadSchedules = () => { invalidateKeys('triggers:schedules'); refreshSchedules() }
  const loadHooks = () => { invalidateKeys('triggers:hooks'); refreshHooks() }
  const loadStores = () => { invalidateKeys('triggers:store'); refreshStores() }
  const loadEvents = () => { invalidateKeys('triggers:events'); refreshEvents() }
  useEffect(() => {
    const t = window.setInterval(refreshSchedules, 10000)
    return () => clearInterval(t)
  }, [refreshSchedules])

  const triggers = useMemo<Trigger[] | null>(() => {
    if (schedules === undefined || hooks === undefined || stores === undefined || events === undefined) return null
    const all = [...schedules.map(scheduleToTrigger), ...hooks.map(hookToTrigger), ...stores.map(storeToTrigger), ...events.map(eventToTrigger)]
    const n = q.trim().toLowerCase()
    return all
      .filter((t) => filter === 'all' || t.kind === filter)
      .filter((t) => !n || `${t.name} ${t.whenLabel} ${t.actionLabel}`.toLowerCase().includes(n))
  }, [schedules, hooks, stores, events, filter, q])

  const open = useMemo(() => triggers?.find((t) => t.id === openId) ?? null, [triggers, openId])

  const counts = useMemo(() => {
    const s = schedules?.length ?? 0, h = hooks?.length ?? 0, st = stores?.length ?? 0, e = events?.length ?? 0
    return { all: s + h + st + e, schedule: s, lifecycle: h, store: st, event: e }
  }, [schedules, hooks, stores, events])

  const loadFailed = triggers === null &&
    !!(schedulesErr || hooksErr || storesErr || eventsErr)

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={<PageTitle>Triggers</PageTitle>}
          right={
            <HeaderActions><HeaderControl icon={Plus} label="New trigger" variant="primary" priority="primary" onClick={() => onCreate()} /></HeaderActions>
          }
        />
      }
      controls={(triggers === null || counts.all > 0)
        ? <ListControls
            results={{ count: (triggers ?? []).length, noun: 'triggers', active: !!q.trim() || filter !== 'all' }}
            search={view === 'list' ? { value: q, onChange: setQ, placeholder: 'Search triggers', label: 'Search triggers' } : undefined}
          >
            <Segmented
              ariaLabel="Triggers view"
              size="sm"
              value={view}
              onChange={setView}
              options={[{ key: 'list', label: 'List', icon: Zap }, { key: 'week', label: 'Week', icon: CalendarDays }]}
            />
            {view === 'list' && <FilterMenu sections={[{
              title: 'Type',
              value: filter,
              defaultKey: 'all',
              onChange: setFilter,
              options: FILTERS.map((f) => ({ key: f.key, label: f.label, count: counts[f.key as keyof typeof counts] })),
            } satisfies FilterSectionDef]} />}
          </ListControls>
        : undefined}
      panel={
        open && (
          <SidePanel key={open.id} fillHeight storeKey="trigger-panel-w" icon={<open.whenIcon size={18} style={{ color: open.whenTone }} />} title={open.name} onClose={() => setQuery({ open: null, edit: null })}>
            {open.kind === 'schedule' && open.schedule
              ? <ScheduleDetail job={open.schedule} editing={editing} onEditingChange={setEditing} onSaved={loadSchedules} onChanged={loadSchedules} onDeleted={() => { setOpenId(""); loadSchedules() }} />
              : open.kind === 'store' && open.store
              ? <StoreTriggerDetail trigger={open.store} onChanged={loadStores} onDeleted={() => { setOpenId(""); loadStores() }} />
              : open.kind === 'event' && open.event
              ? <EventTriggerSummary t={open} onDeleted={() => { setOpenId(""); loadEvents() }} />
              : open.hook
              ? <LifecycleDetail hook={open.hook} providers={providers} editing={editing} onEditingChange={setEditing} onSaved={loadHooks} onDeleted={() => { setOpenId(""); loadHooks() }} />
              : null}
          </SidePanel>
        )
      }
    >
      {view === 'week' ? (
        <WeekGridView onOpenTrigger={(id) => setQuery({ open: id, edit: null, view: 'list' })} />
      ) : (
      <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {loadFailed ? (
          <LoadError what="triggers" error={schedulesErr || hooksErr || storesErr || eventsErr}
            onRetry={() => { loadSchedules(); refreshHooks(); loadStores(); invalidateKeys('triggers:events'); }} />
        ) : triggers === null ? <ListSkeleton rows={6} what="triggers" /> : triggers.length === 0 ? (
              !q && filter === 'all' ? (
                <PresetEmptyState
                  title="No triggers"
                  hint="A trigger runs an action when something happens. Each of these opens the create form already filled in, ready for you to review and save."
                  presets={TRIGGER_PRESETS}
                  onPick={(prefill) => onCreate(prefill.id)}
                  footer={
                    <Button variant="ghost" size="sm" onClick={() => onCreate()}>
                      <Plus size={15} /> Start from scratch
                    </Button>
                  }
                />
              ) : (
              <EmptyState
                icon={Zap}
                title="No matching triggers"
                hint={
                  q && filter !== 'all' ? 'Try a different search or filter.' :
                  q ? 'Try a different search term.' :
                  'Try a different filter.'
                }
              />
              )
            ) : (
              <div className="flex flex-col gap-s">
                {triggers.map((t, i) => {
                  const sd = t.kind === 'store'
                    ? triggerHealthMeta(t.lastStatus, t.state)
                    : t.schedule
                      ? lastRunMeta(t.schedule.last_run_status, t.schedule.last_status)
                      : statusMeta(t.lastStatus)
                  const menuItems: ContextMenuItem[] = [
                    { icon: <Zap size={15} />, label: 'Open', onSelect: () => setQuery({ open: t.id, edit: null }) },
                    ...(t.readOnly || t.kind === 'event' ? [] : [{ icon: <Pencil size={15} />, label: 'Edit', onSelect: () => setQuery({ open: t.id, edit: '1' }) }]),
                  ]
                  return (
                    <ContextMenu key={t.id} items={menuItems}>
                    <ListRow index={i} accent={t.enabled ? t.whenTone : undefined} onClick={() => setQuery({ open: t.id, edit: null })} label={t.name}>
                      <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg" style={{ background: `color-mix(in srgb, ${t.whenTone} 16%, transparent)` }}><t.whenIcon size={19} style={{ color: t.whenTone }} /></span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-s">
                          <span className={`truncate text-[0.9375rem] ${t.enabled ? 'text-on-surface' : 'text-on-surface-var'}`} style={fvs(500)}>{t.name}</span>
                          {!t.enabled && <span className="shrink-0 text-on-surface-low text-[0.75rem]">· disabled</span>}
                          {t.kind === 'schedule' && t.schedule?.is_running && <span className="shrink-0 inline-flex items-center gap-1 text-primary text-[0.75rem]"><span className="relative flex size-1.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-pill bg-primary opacity-60" /><span className="relative inline-flex size-1.5 rounded-pill bg-primary" /></span>running</span>}
                          {
}
                          {
}
                          {t.kind === 'lifecycle' && t.enforcement === 'not_enforcing'
                            ? <span className="shrink-0 inline-flex items-center gap-1 text-warn text-[0.75rem]"><ShieldOff size={11} /> not enforcing</span>
                            : t.kind === 'lifecycle' && eventIsDormant(catalog, t.hook?.event)
                              ? <span data-type="caption" className="shrink-0 text-on-surface-low">· dormant</span>
                              : t.kind === 'lifecycle' && t.usedBy.length === 0 && eventIsAgentScoped(catalog, t.hook?.event) && <span data-type="caption" className="shrink-0 text-on-surface-low">· no agent references this</span>}
                          {t.broken && t.broken.length > 0 && <span className="shrink-0 text-danger text-[0.75rem]">· needs attention</span>}
                          {t.kind === 'store' && t.storeKind && <span className="shrink-0 text-on-surface-low text-[0.75rem]">· {t.storeKind}</span>}
                          {
}
                          {t.readOnly && <span className="shrink-0 inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-px text-on-surface-var text-[0.75rem]"><Users size={11} /> {t.author || 'shared'}</span>}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low text-[0.8125rem]">
                          <span className="inline-flex items-center gap-1" style={{ color: t.whenTone }}><t.whenIcon size={11} /> {t.whenLabel}</span>
                          <span className="inline-flex items-center gap-1"><t.actionIcon size={11} /> {t.actionLabel}</span>
                          {t.kind === 'schedule' && t.enabled && t.schedule?.next_run_ts && <span className="inline-flex items-center gap-1"><Clock size={11} /> {relFuture(t.schedule.next_run_ts)}</span>}
                          {
}
                          {t.actionProvider && rungByProvider.get(t.actionProvider) && (
                            <RungChip type={rungByProvider.get(t.actionProvider)!} ladder={ladder} />
                          )}
                          {t.kind === 'lifecycle' && t.runCount != null && <span>ran {t.runCount}×</span>}
                          { }
                          {t.kind === 'event' && t.runCount != null && <span>fired {t.runCount}×</span>}
                        </div>
                      </div>
                      <div className="hidden sm:flex shrink-0 items-center gap-1.5 text-on-surface-low text-[0.75rem]">
                        <sd.icon size={13} style={{ color: sd.tone }} />
                        <span>{t.lastRunTs ? relPast(t.lastRunTs) : 'never'}</span>
                      </div>
                    </ListRow>
                    </ContextMenu>
                  )
                })}
              </div>
            )}
      </div>
      )}
    </WorkbenchLayout>
  )
}

export function EventTriggerSummary({ t, onDeleted }: { t: Trigger; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false)
  const pm = eventPatternMeta(t.eventPattern)
  const rows: Array<[string, string]> = [
    ['Fires on', pm.label],
    ...(pm.matcher ? [[pm.matcherLabel, t.eventMatcher || 'anything'] as [string, string]] : []),
    ['Then', t.actionLabel],
    ['Fired', t.runCount != null ? `${t.runCount}×` : '—'],
  ]
  async function remove() {
    if (!(await confirmDelete('data-event trigger', t.name))) return
    setBusy(true)
    try {
      if (!(await reportingWrite(`delete ${t.name}`, () => api.deleteEventTrigger(t.rawId)))) return
      onDeleted()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-col gap-l p-l">
      <p className="text-on-surface-var text-[0.8125rem]">{pm.desc}</p>
      {rows.map(([label, value]) => (
        <div key={label}>
          <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{label}</div>
          <div data-type="body-m" className="text-on-surface break-words">{value}</div>
        </div>
      ))}
      <p className="text-on-surface-low text-[0.8125rem]">
        Editing a data-event trigger isn’t available here yet — recreate it to change its pattern.
      </p>
      {
}
      {!t.readOnly && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <div className="flex-1" />
          <Button variant="ghost" size="sm" onClick={remove} disabled={busy} disabledReason={BUSY_REASON} className="text-danger">
            <Trash2 size={14} /> Delete
          </Button>
        </div>
      )}
    </div>
  )
}

