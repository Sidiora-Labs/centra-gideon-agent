import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Plus, List, LayoutGrid, GitFork, Columns3, MessageSquare, FolderKanban, X, RotateCcw, ListChecks, Target, Code2, Check, CheckCircle2, Trash2, Users, UserRound, Search, Filter } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../shared/ui/HeaderActions'
import { FilterMenu, type FilterSectionDef } from '../../shared/ui/FilterMenu'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { Meter } from '../../shared/ui/Meter'
import { SearchField } from '../../shared/ui/SearchField'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { TextLink } from '../../shared/ui/TextLink'
import { confirm, confirmDelete } from '../../shared/ui/dialog'
import { SidePanel } from '../../shared/ui/SidePanel'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { spring, expr } from '../../shared/theme/motion'
import { api, type TaskItem, type TaskListItem } from '../../shared/data/api'
import { statusMeta, signalPriority, dueMeta, TERMINAL, ListChecksLike, exitDoneCount } from './taskMeta'
import { TaskDetail } from './TaskDetail'
import { TaskGraph } from './TaskGraph'
import { TaskBoard } from './TaskBoard'
import { PageTitle } from '../../shared/ui/PageTitle'
import { RowHitTarget } from '../../shared/ui/RowHitTarget'
import { accentChip } from '../../shared/theme/accent'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { ASSIGNED_EVERYONE, ASSIGNED_MINE, FULL_WIDTH, GOAL_LOOPS_PROJECT, SCOPE_CODING, SCOPE_GOALS, belongsToOwner, filterTaskCollection, scopeContains, useTaskCollection, useTaskPreference, type TaskView } from './taskCollectionState'

const viewOptions = [{ key: 'list', label: 'List view', icon: List }, { key: 'cards', label: 'Cards view', icon: LayoutGrid }, { key: 'board', label: 'Kanban board', icon: Columns3 }, { key: 'dag', label: 'Dependency graph', icon: GitFork }]
const statusOptions = [{ key: 'all', label: 'All' }, { key: 'ready', label: 'Ready' }, ...['open', 'in_progress', 'blocked', 'done'].map(key => ({ key, label: statusMeta(key).label }))]
const sortOptions = [{ key: 'recent', label: 'Recently updated' }, { key: 'due', label: 'Due date' }, { key: 'priority', label: 'Priority' }]

export function TasksListPage({ onCreate, view: viewProp, filter, openId, setView: changeView, setFilter, setOpenId, editing, setEditing,
  q: query, sort: sortProp, scope: scopeProp, list: listProp, setQ, setSort, setScope: changeScope, setList }: {
  onCreate: () => void; view: string; filter: string; openId: string | null
  setView: (v: string) => void; setFilter: (f: string) => void; setOpenId: (id: string | null) => void
  editing: boolean; setEditing: (v: boolean) => void; q: string; sort: string; scope: string; list: string
  setQ: (v: string) => void; setSort: (v: string) => void; setScope: (v: string) => void; setList: (v: string) => void
}) {
  const q = query.trim()
  const collection = useTaskCollection(q, filter)
  const { tasks, owner, projects, lists, coding, ready, results, selected, bulkBusy, runBulk, clearSelection, toggleSelect, moveError } = collection
  const [savedView, setView] = useTaskPreference(viewProp, 'tasks-view', 'list', changeView)
  const view = (viewOptions.some(option => option.key === savedView) ? savedView : 'list') as TaskView
  const [sortBy, setSortBy] = useTaskPreference(sortProp, 'tasks-sort', 'recent', setSort)
  const [scope, setScope] = useTaskPreference(scopeProp, 'tasks-scope', '', changeScope)
  const [assigned, setAssigned] = useState(ASSIGNED_EVERYONE)
  const isProjectScope = !!scope && scope !== SCOPE_GOALS && scope !== SCOPE_CODING
  const scopedProject = isProjectScope ? projects.find(project => project.name === scope) : undefined
  const projectLists = lists.filter(list => list.project_id === scopedProject?.id)
  const listFilter = listProp ? { id: listProp, name: lists.find(list => list.id === listProp)?.name ?? listProp } : null
  const setListFilter = (list: { id: string; name: string } | null) => setList(list?.id ?? '')
  useEffect(() => { if (!isProjectScope && listProp) setList('') }, [isProjectScope, listProp, setList])
  const scopedTasks = useMemo(() => (tasks ?? []).filter(task => scopeContains(task, scope, coding)), [tasks, scope, coding])
  const filtered = useMemo(() => filterTaskCollection({ tasks, ready, results, query: q, status: filter, scope, coding, list: listProp, owner, assigned, sort: sortBy }), [tasks, ready, results, q, filter, scope, coding, listProp, owner, assigned, sortBy])
  const sections: FilterSectionDef[] = useMemo(() => {
    const counts = new Map<string, number>()
    let goals = 0, codeTasks = 0, foreign = 0
    for (const task of tasks ?? []) {
      if (task.project) counts.set(task.project, (counts.get(task.project) ?? 0) + 1)
      if (task.project === GOAL_LOOPS_PROJECT) goals++
      if (task.project && coding.has(task.project)) codeTasks++
      if (owner && !belongsToOwner(task, owner)) foreign++
    }
    const available: FilterSectionDef[] = [{ title: 'Scope', value: scope, defaultKey: '', onChange: setScope, options: [
      { key: '', label: 'All tasks', icon: ListChecks, count: tasks?.length }, { key: SCOPE_GOALS, label: 'Goals', icon: Target, count: goals },
      ...(coding.size ? [{ key: SCOPE_CODING, label: 'Coding projects', icon: Code2, count: codeTasks }] : []),
      ...projects.filter(project => project.name !== GOAL_LOOPS_PROJECT).sort((left, right) => left.name.localeCompare(right.name)).map((project, index) => ({ key: project.name, label: project.name, icon: FolderKanban, count: counts.get(project.name), groupLabel: index === 0 ? 'Projects' : undefined })),
    ] }]
    if (!FULL_WIDTH.includes(view) && !q) available.push({ title: 'Status', value: filter, defaultKey: 'all', onChange: setFilter, options: statusOptions.map(option => ({ ...option, count: option.key === 'all' ? tasks?.length : option.key === 'ready' ? ready?.length : tasks?.filter(task => option.key === 'done' ? TERMINAL.has(task.status) : task.status === option.key).length })) })
    if (owner && foreign) available.push({ title: 'Assigned', value: assigned, defaultKey: ASSIGNED_EVERYONE, onChange: setAssigned, options: [{ key: ASSIGNED_EVERYONE, label: 'Everyone', icon: Users, count: tasks?.length }, { key: ASSIGNED_MINE, label: 'Mine', icon: UserRound, count: (tasks?.length ?? 0) - foreign }] })
    if (!FULL_WIDTH.includes(view)) available.push({ title: 'Sort by', value: sortBy, defaultKey: 'recent', onChange: setSortBy, options: sortOptions })
    return available
  }, [tasks, ready, projects, coding, owner, scope, view, q, filter, assigned, sortBy, setScope, setFilter, setSortBy])
  async function resetList(list: TaskListItem) {
    if (!(await confirm({
      title: `Reset “${list.name}”?`,
      body: 'Every task goes back to open and its exit criteria are marked incomplete. '
        + 'Each task’s execution notes are cleared, and those cannot be recovered.',
      danger: true,
      confirmLabel: 'Reset list',
    }))) return
    collection.showError('')
    try { await api.resetTaskList(list.id); collection.load() }
    catch (error) { collection.showError(`Reset “${list.name}”: ${error instanceof Error ? error.message : 'Could not reset the list.'}`) }
  }
  async function moveTask(id: string, status: string) { await collection.moveTask(id, status) }
  const open = tasks?.find(task => task.id === openId)
  const openStatus = open ? statusMeta(open.status) : null
  let body: ReactNode
  if (q && collection.searchError) body = <LoadError what="search results" error={collection.searchError} onRetry={collection.retrySearch} />
  else if (filter === 'ready' && !q && collection.readyError) body = <LoadError what="ready tasks" error={collection.readyError} onRetry={collection.retryReady} />
  else if (tasks === null && collection.loadError) body = <LoadError what="tasks" error={collection.loadError} onRetry={collection.load} />
  else if (filtered === null) body = <ListSkeleton rows={6} what="tasks" />
  else if (!tasks?.length) body = <EmptyState icon={ListChecksLike} title="No tasks" hint="Break a goal into tracked work. Create a task, or let an agent plan from a chat." action={{ label: 'New task', onClick: onCreate, icon: Plus }} />
  else if (FULL_WIDTH.includes(view)) body = !scopedTasks.length ? <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." /> : view === 'board' ? <TaskBoard tasks={scopedTasks} onOpen={setOpenId} onMove={moveTask} /> : <TaskGraph tasks={scopedTasks} onOpen={setOpenId} />
  else if (!filtered.length) {
    if (q) body = <EmptyState icon={Search} title={`No tasks match “${q}”`} hint={`You have ${tasks?.length ?? 0} task${tasks.length === 1 ? '' : 's'} — just none matching the search.`} action={{ label: 'Clear search', onClick: () => setQ('') }} />
    else if (filter !== 'all' || listFilter || (owner && assigned === ASSIGNED_MINE)) body = <EmptyState icon={Filter} title="No tasks in this view" hint={`You have ${tasks?.length ?? 0} task${tasks.length === 1 ? '' : 's'} — just none in this view.`} action={{ label: 'View all tasks', onClick: () => { setFilter('all'); setListFilter(null); setAssigned(ASSIGNED_EVERYONE) } }} />
    else body = <EmptyState icon={ListChecksLike} title="Nothing here" hint="No tasks match this scope." />
  } else body = view === 'list' ? <div className="grid gap-s pb-16">{filtered.map((t, index) => <TaskRow key={t.id} t={t} index={index} onOpen={() => setOpenId(t.id)} onProject={setScope} selected={selected.has(t.id)} selecting={selected.size > 0} onToggleSelect={() => toggleSelect(t.id)} onComplete={() => moveTask(t.id, 'done')} />)}</div> : <div className="grid grid-cols-1 gap-m sm:grid-cols-2">{filtered.map((t, index) => <TaskCard key={t.id} t={t} index={index} onOpen={() => setOpenId(t.id)} onProject={setScope} />)}</div>
  return <WorkbenchLayout scroll={false}
    topBar={<TopBar keepCornerPadding left={<PageTitle>Tasks</PageTitle>} right={<HeaderActions><HeaderSegmented ariaLabel="View" value={view} onChange={setView} options={viewOptions} /><HeaderControl icon={Plus} label="New task" variant="primary" priority="primary" onClick={onCreate} /></HeaderActions>} />}
    controls={<div className="shrink-0 border-b border-outline-variant/30 bg-surface-container/20"><div className="mx-auto flex w-full items-center gap-s px-l py-m" style={{ maxWidth: 'var(--content-width)' }}>
      {!FULL_WIDTH.includes(view) && <div className="min-w-0 flex-1"><SearchField value={query} onChange={setQ} placeholder="Search tasks" ariaLabel="Search tasks" /></div>}
      <FilterMenu sections={sections} /><ResultAnnouncement count={filtered?.length ?? 0} noun="tasks" active={q.length > 0} />
    </div></div>}
    panel={open && openStatus && <SidePanel key={open.id} fillHeight storeKey="task-panel-w" icon={<openStatus.icon size={18} style={{ color: openStatus.tone }} />} title={open.title} onClose={() => setOpenId(null)}><TaskDetail task={open} editing={editing} onEditingChange={setEditing} allTasks={tasks ?? []} onOpenTask={setOpenId} onSaved={collection.patchLocal} onDeleted={() => { setOpenId(null); collection.load() }} /></SidePanel>}>
    <div className={view === 'board' ? 'flex min-h-0 flex-1 flex-col px-l py-l' : 'min-h-0 flex-1 overflow-y-auto'} tabIndex={view === 'dag' ? 0 : undefined} role={view === 'dag' ? 'group' : undefined} aria-label={view === 'dag' ? 'Dependency graph' : undefined}>
      <div className={`mx-auto w-full ${view === 'board' ? 'flex min-h-0 flex-1 flex-col' : 'px-l py-l'}`} style={{ maxWidth: 'var(--content-width)' }}>
        {moveError && <div className="mb-s shrink-0"><InlineError animated icon onDismiss={() => collection.showError('')}>{moveError}</InlineError></div>}
        {view !== 'board' && isProjectScope && projectLists.length > 0 && <TaskListBar lists={projectLists} active={listProp} repeatableId={projects.find(project => project.name === 'Repeatable')?.id} onPick={list => setListFilter(listProp === list.id ? null : list)} onReset={resetList} />}
        {body}
      </div>
    </div>
    {selected.size > 0 && <div className="pointer-events-none fixed inset-x-0 bottom-6 z-30 flex justify-center px-l"><div className="pointer-events-auto flex flex-wrap items-center gap-s rounded-lg border border-outline-variant/40 bg-surface-highest/95 px-m py-s shadow-sheet backdrop-blur">
      <span data-type="label-s" className="font-semibold text-on-surface tabular-nums">{selected.size} selected</span><span className="h-4 w-px bg-outline-variant/50" aria-hidden />
      <Button size="sm" variant="ghost" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('update', { status: 'done' })}><CheckCircle2 size={14} /> Complete</Button>
      <Button size="sm" variant="ghost" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={async () => { if (await confirmDelete('task', `${selected.size} tasks`)) void runBulk('delete') }}><Trash2 size={14} /> Delete</Button>
      <button type="button" onClick={clearSelection} aria-label="Clear selection" className="grid size-7 place-items-center rounded-md text-on-surface-low hover:bg-surface-container hover:text-on-surface"><X size={15} /></button>
    </div></div>}
  </WorkbenchLayout>
}

function TaskListBar({ lists, repeatableId, active, onPick, onReset }: { lists: TaskListItem[]; repeatableId?: string; active: string; onPick: (list: TaskListItem) => void; onReset: (list: TaskListItem) => void }) {
  return <div className="mb-m flex flex-wrap items-center gap-s"><span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low uppercase tracking-wide"><ListChecks size={12} /> Task lists</span>{lists.map(list => {
    const picked = active === list.id
    return <span key={list.id} data-type="body-s" className={`inline-flex min-h-8 items-center gap-1 rounded-md border px-s ${picked ? 'border-primary bg-primary text-on-primary' : 'border-outline-variant/30 bg-surface-container text-on-surface-var'}`}>
      <button type="button" aria-label={`Task list: ${list.name}`} aria-pressed={picked} onClick={() => onPick(list)} className="min-h-6">{list.name}</button>
      {!!repeatableId && list.project_id === repeatableId && <button type="button" aria-label={`Reset list ${list.name}`} title="Reset this repeatable list (all tasks must be done)" onClick={event => { event.stopPropagation(); onReset(list) }} className="grid size-6 place-items-center rounded-md hover:brightness-125"><RotateCcw size={12} /></button>}
    </span>
  })}</div>
}

function MetaLine({ t, onProject }: { t: TaskItem; onProject?: (project: string) => void }) {
  const pm = signalPriority(t.priority), due = dueMeta(t.due)
  const who = t.assignee?.trim() || t.author?.trim()
  const exit = t.exit_criteria ?? []
  const lead: ReactNode[] = [
    pm ? <span key="priority" style={{ color: pm.tone }}>{pm.label}</span> : null,
    who ? <span key="person" className="inline-flex items-center gap-1" title={t.assignee?.trim() ? `Assigned to ${who}` : `Created by ${who}`}><UserRound size={11} />{who}</span> : null,
    t.project ? <TextLink key="project" onClick={event => { event.stopPropagation(); onProject?.(t.project!) }} icon={FolderKanban} iconSize={11} title={`Filter by project “${t.project}”`}>{t.project}</TextLink> : null,
  ].filter(Boolean)
  const tail: ReactNode[] = [due ? <span key="due" style={{ color: due.tone }}>{due.label}</span> : null, exit.length ? <span key="criteria">{exitDoneCount(exit)}/{exit.length} criteria</span> : null].filter(Boolean)
  const comments = (t.comment_count ?? 0) > 0 ? <span className="inline-flex items-center gap-1"><MessageSquare size={11} />{t.comment_count}</span> : null
  if (lead.length === 0 && tail.length === 0 && !comments) return null
  // Gaps keep metadata readable at 320px (WCAG 1.4.10 Reflow).
  return <div data-type="body-s" className="mt-1 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low">{lead}{tail}{comments}</div>
}
function TaskRow({ t, index, onOpen, onProject, selected, selecting, onToggleSelect, onComplete }: {
  t: TaskItem; index: number; onOpen: () => void; onProject?: (project: string) => void; selected?: boolean; selecting?: boolean; onToggleSelect?: () => void; onComplete?: () => void
}) {
  const sm = statusMeta(t.status), done = TERMINAL.has(t.status)
  const reduced = useReducedMotion()
  const actions: ContextMenuItem[] = [{ icon: <MessageSquare size={15} />, label: 'Open', onSelect: onOpen }]
  if (!done && t.provider !== 'project' && onComplete) actions.push({ icon: <CheckCircle2 size={15} />, label: 'Complete', onSelect: onComplete })
  if (onToggleSelect) actions.push({ icon: <Check size={15} />, label: selected ? 'Deselect' : 'Select', onSelect: onToggleSelect })
  return <ContextMenu items={actions}><motion.div initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: reduced ? 0 : Math.min(index * 0.03, 0.3) }} onClick={onOpen} tabIndex={-1}
    className="group relative flex cursor-pointer items-center gap-m rounded-lg border border-outline-variant/25 bg-surface-container/60 px-l py-m transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary" style={selected ? { outline: '1.5px solid var(--color-primary)', outlineOffset: -1.5 } : undefined}>
    <RowHitTarget label={`${t.title} — ${sm.label}`} />
    <button type="button" aria-label={`${selected ? 'Deselect' : 'Select'}: ${t.title}`} onClick={event => { event.stopPropagation(); onToggleSelect?.() }} className="-m-0.5 grid size-6 shrink-0 place-items-center"><span className={`grid size-5 place-items-center rounded-md border ${selected ? 'border-primary bg-primary text-on-primary' : `border-outline-variant text-transparent ${selecting ? '' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}`}`}><Check size={13} /></span></button>
    <sm.icon size={20} className="shrink-0" style={{ color: sm.tone }} /><div className="min-w-0 flex-1"><span className={`block truncate text-[0.9375rem] font-medium ${done ? 'text-on-surface-low line-through' : 'text-on-surface'}`} title={t.title}>{t.title}</span><MetaLine t={t} onProject={onProject} /></div>
    {!!t.labels?.length && <div className="hidden shrink-0 gap-1 md:flex">{t.labels.slice(0, 2).map(label => <span key={label} data-type="caption" className="rounded-md bg-surface-high px-2 py-1 text-on-surface-var">{label}</span>)}</div>}
  </motion.div></ContextMenu>
}
function TaskCard({ t, index, onOpen, onProject }: { t: TaskItem; index: number; onOpen: () => void; onProject?: (project: string) => void }) {
  const sm = statusMeta(t.status), pm = signalPriority(t.priority), due = dueMeta(t.due)
  const reduced = useReducedMotion()
  const exit = t.exit_criteria ?? [], exitDone = exitDoneCount(exit)
  const badges = [sm, pm, due].filter((badge): badge is { label: string; tone: string } => !!badge)
  return <motion.div initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: reduced ? 0 : Math.min(index * 0.03, 0.3) }} whileHover={reduced ? undefined : { y: -expr(4, 0.3), boxShadow: 'var(--shadow-lift)' }} whileTap={reduced ? undefined : { scale: 1 - expr(0.012, 0.3) }} onClick={onOpen} tabIndex={-1}
    className="group relative grid cursor-pointer gap-m rounded-lg border border-outline-variant/30 bg-surface-container/60 p-l transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
    <RowHitTarget label={t.title} /><div className="flex items-start gap-s"><sm.icon size={18} style={{ color: sm.tone }} className="mt-0.5 shrink-0" /><span data-type="label-m" className={`min-w-0 flex-1 font-medium leading-snug ${TERMINAL.has(t.status) ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{t.title}</span>{t.assignee && <span data-type="caption" title={`Assigned to ${t.assignee}`} className="rounded-md bg-surface-high px-2 py-1 text-on-surface-var">@{t.assignee}</span>}</div>
    <div className="flex flex-wrap items-center gap-1.5">{badges.map((badge, position) => <span key={position} data-type="caption" className="rounded-md px-2 py-1" style={{ color: badge.tone, background: `color-mix(in srgb, ${badge.tone} 16%, transparent)` }}>{badge.label}</span>)}
      {t.project && <button type="button" onClick={event => { event.stopPropagation(); onProject?.(t.project!) }} title={`Filter by project “${t.project}”`} data-type="caption" className="inline-flex min-h-6 items-center gap-1 rounded-md px-2 hover:brightness-125" style={accentChip}><FolderKanban size={10} />{t.project}</button>}
      {(t.labels ?? []).slice(0, 2).map(label => <span key={label} data-type="caption" className="rounded-md bg-surface-high px-2 py-1 text-on-surface-var">{label}</span>)}
    </div>
    {exit.length > 0 && <div className="flex items-center gap-s"><Meter size="thin" className="flex-1" tone="var(--color-ok)" label={`Exit criteria: ${exitDone} of ${exit.length} met`} pct={exitDone / exit.length * 100} /><span data-type="caption" className="text-on-surface-low tabular-nums">{exitDone}/{exit.length}</span></div>}
  </motion.div>
}
