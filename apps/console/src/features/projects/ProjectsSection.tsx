import { useEffect, useId, useReducer, useState } from 'react'
import { useProjectCollection } from './projectCollectionState'
import { useProjectDetailState } from './projectDetailState'
import { useProjectPeek, useProjectDirectory, projectDirectoryError } from './projectPanelState'
import { MoreRow } from '../../shared/ui/MoreRow'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { AnimatePresence, motion } from 'framer-motion'
import { ContextMenu, type ContextMenuItem } from '../../shared/ui/motion'
import { spring } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import { FolderKanban, Search, Plus, Loader2, Trash2, FolderOpen, Folder, FolderTree, File as FileIcon, X, ChevronRight, ChevronDown, Pencil, Check, ListChecks, Lock, FileBox, Star, MessageSquare, Repeat, Target, Code2, Telescope, Palette, FileText, CircleDot, Circle, AlertTriangle, RefreshCw, Download, BookMarked, Users, type LucideIcon } from 'lucide-react'
import { statusMeta, TERMINAL } from '../tasks/taskMeta'
import { Popover, MenuRow } from '../../shared/ui/Popover'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { IconButton } from '../../shared/ui/IconButton'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { ListControls } from '../../shared/ui/ListControls'
import { ListSkeleton, EmptyState, LoadError } from '../../shared/ui/ListScaffold'
import { RowHitTarget } from '../../shared/ui/RowHitTarget'
import { Modal } from '../../shared/ui/Modal'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Button } from '../../shared/ui/Button'
import { FieldHintProvider, FieldLabelProvider, TextArea, TextInput } from '../../shared/ui/forms'
import { InlineError } from '../../shared/ui/InlineError'
import { WorkspacePicker } from '../code/WorkspacePicker'
import { api, type ProjectItem, type TaskListItem, type LoopKind, type TaskItem, type FsEntry, type WorkRow, type WorkState, type WorkBoard, type ProjectKnowledgeItem, type SharingPolicy } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { setActiveProject } from '../../shared/data/activeProject'
import { PageTitle } from '../../shared/ui/PageTitle'

export function ProjectsSection({ sub, navigate, query, setQuery }: RouteProps) {
  const route = sub || ''
  const segmentEnd = route.indexOf('/')
  const seg = segmentEnd < 0 ? route : route.slice(0, segmentEnd)
  if (seg) return <ProjectDetailPage id={seg} onBack={() => navigate('projects')} navigate={navigate} query={query} setQuery={setQuery} />
  return <ProjectListPage onOpen={(id) => navigate(`projects/${id}`)} query={query} setQuery={setQuery} />
}

function ProjectListPage({ onOpen, query, setQuery }: { onOpen: (id: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { projects, loading, loadErr, refresh, creating, setCreating, busy, err, setErr, activeId, create, del } = useProjectCollection(onOpen)

  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })

  const [peekId, setPeekId] = useQueryParam(query, setQuery, 'peek', '')
  const peekProject = peekId ? (projects ?? []).find((p) => p.id === peekId) ?? null : null
  const needle = q.trim().toLowerCase()
  const shown = (projects ?? []).filter((p) => !needle || p.name.toLowerCase().includes(needle))

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        keepCornerPadding
        left={<div className="flex items-center gap-2"><FolderKanban size={18} className="text-primary" /><PageTitle>Projects</PageTitle></div>}
        right={<HeaderActions><HeaderControl icon={Plus} label="New project" onClick={() => setCreating(true)} variant="primary" priority="primary" /></HeaderActions>} />

      {!!projects?.length && (
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search projects', label: 'Search projects' }}
          results={{ count: shown.length, noun: 'projects', active: !!needle }} />
      )}

      {err && (
        <InlineError className="mx-l mt-2" onDismiss={() => setErr(null)}>{err}</InlineError>
      )}

      {creating && (
        <NewProjectModal busy={busy} onClose={() => setCreating(false)} onCreate={create} />
      )}

      <div className="flex min-h-0 flex-1">
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-l py-l">

        {projects === undefined && loadErr ? <LoadError what="projects" error={loadErr} onRetry={() => { invalidateKeys('projects:list'); refresh() }} />
          : loading && !projects ? <ListSkeleton rows={5} what="projects" />
          : !shown.length ? (
            needle

              ? <EmptyState icon={Search} title="No matching projects" hint="Try a different term." />
              : <EmptyState icon={FolderKanban} title="No projects yet"
                  hint="A project ties your loops (General, Goal, Code, Design), chats, and tasks into one context-continuous unit."
                  action={{ label: 'New project', onClick: () => setCreating(true), icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-2">
              {shown.map((project, index) => <ProjectCollectionRow key={project.id} project={project} index={index}
                active={project.id === activeId} peeked={project.id === peekId}
                onPeek={() => setPeekId(peekId === project.id ? '' : project.id)} onOpen={() => onOpen(project.id)} onDelete={() => del(project)} />)}
            </div>
          )}
      </div>

      <AnimatePresence>
        {peekId && (
          <SidePanel key={peekId} fillHeight storeKey="project-peek-w" urlKey={{ key: 'peek', setQuery }}
            icon={<FolderKanban size={18} className="text-primary" />}
            title={peekProject?.name || 'Project'}
            onExpand={() => onOpen(peekId)}
            onClose={() => setPeekId('')}>
            <ProjectPeekBody id={peekId} project={peekProject} onOpen={() => onOpen(peekId)} />
          </SidePanel>
        )}
      </AnimatePresence>
      </div>

    </div>
  )
}

function ProjectCollectionRow({ project, index, active, peeked, onPeek, onOpen, onDelete }: {
  project: ProjectItem; index: number; active: boolean; peeked: boolean
  onPeek: () => void; onOpen: () => void; onDelete: () => void
}) {
  const entries: ContextMenuItem[] = [
    { icon: <FolderKanban size={15} />, label: peeked ? 'Close peek' : 'Peek', onSelect: onPeek },
    { icon: <FolderOpen size={15} />, label: 'Open full page', onSelect: onOpen },
  ]
  if (!project.is_builtin) entries.push({ icon: <Trash2 size={15} />, label: 'Delete', onSelect: onDelete, danger: true })
  const badges = [project.is_builtin ? 'Built-in' : '', project.status === 'archived' ? 'Archived' : ''].filter(Boolean)
  const workspace = project.workspace_dir ? project.workspace_dir.split('/').slice(-2).join('/') : 'no workspace'
  const lists = typeof project.task_list_count === 'number' ? ` · ${project.task_list_count} list${project.task_list_count === 1 ? '' : 's'}` : ''
  return <ContextMenu items={entries}>
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      tabIndex={-1} onClick={onPeek} className="group relative flex cursor-pointer items-center gap-m rounded-xl border border-outline/25 bg-surface-container/60 px-l py-m text-left transition-colors hover:bg-surface-high has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
      <RowHitTarget label={project.name} />
      <FolderKanban size={17} className="shrink-0 text-on-surface-low" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-s">
          {active && <Star size={12} className="shrink-0 text-primary" style={{ fill: 'var(--color-primary)' }} aria-label="Active project" />}
          <span className="truncate text-[0.9375rem] text-on-surface">{project.name}</span>
          {badges.map(label => <span key={label} className="shrink-0 rounded-md bg-surface-high px-1.5 py-0.5 text-[0.75rem] text-on-surface-low">{label}</span>)}
        </div>
        <p className="mt-1 truncate text-[0.75rem] text-on-surface-low">{workspace}{lists}</p>
      </div>
      {!project.is_builtin && <SquareIconButton icon={Trash2} tone="danger" label={`Delete project: ${project.name}`} title="Delete project"
        onClick={event => { event.stopPropagation(); onDelete() }} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" />}
      <ChevronRight size={15} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
    </motion.div>
  </ContextMenu>
}

function ProjectPeekBody({ id, project, onOpen }: { id: string; project: ProjectItem | null; onOpen: () => void }) {
  const { linked, taskLists } = useProjectPeek(id)
  const statusTone = (status: string) => ({ running: 'ok', active: 'ok', stopped: 'danger', failed: 'danger', paused: 'warn', blocked: 'warn' } as Record<string, string>)[status] || 'on-surface-low'
  const groups = linked ? [
    { label: 'Loops & Code', maximum: 10, rows: [...linked.code, ...linked.loops].map(item => ({ id: item.id, title: item.name, detail: item.status, icon: CircleDot, tone: `var(--color-${statusTone(item.status)})` })) },
    { label: 'Chats', maximum: 8, rows: linked.chats.map(item => ({ id: item.key, title: item.title || item.key, detail: item.running ? 'running' : '', icon: MessageSquare, tone: item.running ? 'var(--color-primary)' : 'var(--color-on-surface-low)' })) },
    { label: 'Artifacts', maximum: 8, rows: linked.artifacts.map(item => ({ id: item.slug, title: item.name, detail: item.kind, icon: FileBox, tone: 'var(--color-on-surface-low)' })) },
  ] : []
  return <div className="flex h-full min-h-0 flex-col gap-l">
    <div className="grid min-h-0 flex-1 content-start gap-l overflow-y-auto">
      {project?.brief && <PeekSection label="Brief"><p className="whitespace-pre-wrap text-[0.8125rem] leading-relaxed text-on-surface-var">{project.brief}</p></PeekSection>}
      <PeekSection label="Workspace"><p className="break-all font-mono text-[0.8125rem] text-on-surface-var">{project?.workspace_dir || 'No workspace bound'}</p></PeekSection>
      {project?.status && <PeekSection label="Status"><span className="inline-flex h-6 items-center gap-1.5 rounded-md px-2 text-[0.75rem]"
        style={{ background: `color-mix(in srgb, var(--color-${statusTone(project.status)}) 14%, transparent)`, color: `var(--color-${statusTone(project.status)})` }}>
        <Circle size={6} style={{ fill: 'currentColor' }} />{project.status}
      </span></PeekSection>}
      {project?.created_at && <PeekSection label="Created"><p className="text-[0.8125rem] text-on-surface-var">{new Date(project.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</p></PeekSection>}
      {taskLists.length > 0 && <PeekSection label={`Task lists · ${taskLists.length}`}>
        <div className="grid gap-s">{taskLists.slice(0, 6).map(list => <div key={list.id} className="flex items-center gap-s rounded-lg bg-surface-container px-m py-s">
          <ListChecks size={13} className="shrink-0 text-on-surface-low" /><span className="min-w-0 flex-1 truncate text-[0.8125rem] text-on-surface">{list.name}</span>
        </div>)}<MoreRow total={taskLists.length} shown={6} /></div>
      </PeekSection>}
      {!linked ? <ListSkeleton rows={3} /> : <>
        {groups.filter(group => group.rows.length > 0).map(group => <PeekSection key={group.label} label={`${group.label} · ${group.rows.length}`}>
          <div className="grid gap-s">{group.rows.slice(0, group.maximum).map(row => <div key={row.id} className="flex items-center gap-s rounded-lg bg-surface-container px-m py-s">
            <row.icon size={13} className="shrink-0" style={{ color: row.tone }} />
            <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-on-surface">{row.title}</span>
            {row.detail && <span className="shrink-0 text-[0.75rem]" style={{ color: row.tone }}>{row.detail}</span>}
          </div>)}<MoreRow total={group.rows.length} shown={group.maximum} /></div>
        </PeekSection>)}
        {linked.knowledge.length > 0 && <PeekSection label={`Knowledge · ${linked.knowledge.length}`}><ProjectKnowledgeList items={linked.knowledge} /></PeekSection>}
      </>}
    </div>
    <Button size="sm" onClick={onOpen} className="shrink-0">Open project <ChevronRight size={13} /></Button>
  </div>
}
function PeekSection({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="grid gap-s"><h3 className="text-[0.75rem] text-on-surface-low" style={fvs(500)}>{label}</h3>{children}</section>
}

export const SHARING_POLICY_LABEL: Record<SharingPolicy, string> = {
  private: 'Private',
  shared: 'Shared',
}

function sharingPolicyLabel(policy: SharingPolicy): string {
  if (Object.prototype.hasOwnProperty.call(SHARING_POLICY_LABEL, policy)) return SHARING_POLICY_LABEL[policy]
  console.warn(`unmapped sharing_policy from the wire: ${policy}`)
  const words = String(policy).split('_').join(' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function ProjectKnowledgeList({ items }: { items: ProjectKnowledgeItem[] }) {
  const visible = items.slice(0, 8)
  return <div className="grid gap-s">
    {visible.map(item => <article key={item.id} className="flex items-center gap-s rounded-lg border border-outline/20 bg-surface-container px-m py-s">
      <BookMarked size={13} className="shrink-0 text-on-surface-low" />
      <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-on-surface">{item.title || item.kind || item.id}</span>
      {item.source_project && <span title={`Shared from ${item.source_project}`} className="inline-flex shrink-0 items-center gap-1 text-[0.75rem] text-on-surface-low">
        <Users size={11} />{item.source_project}
      </span>}
      <span className="shrink-0 rounded-md bg-surface-high px-1.5 text-[0.75rem] text-on-surface-low">{sharingPolicyLabel(item.sharing_policy)}</span>
    </article>)}
  </div>
}

function NewProjectModal({ busy, onClose, onCreate }: {
  busy: boolean
  onClose: () => void
  onCreate: (form: { name: string; brief: string; workspaceDir: string; setActive: boolean }) => void
}) {
  const [draft, update] = useReducer((state: { name: string; brief: string; workspaceDir: string; setActive: boolean; pickWs: boolean }, patch: Partial<typeof state>) => ({ ...state, ...patch }), { name: '', brief: '', workspaceDir: '', setActive: true, pickWs: false })
  const { name, brief, workspaceDir, setActive, pickWs } = draft
  const setName = (name: string) => update({ name })
  const setBrief = (brief: string) => update({ brief })
  const setWorkspaceDir = (workspaceDir: string) => update({ workspaceDir })
  const setSetActive = (setActive: boolean) => update({ setActive })
  const setPickWs = (pickWs: boolean) => update({ pickWs })
  const submit = () => { if (name.trim() && !busy) onCreate({ name, brief, workspaceDir, setActive }) }
  return (
    <Modal title="New project" icon={<FolderKanban size={18} className="text-primary" />} onClose={onClose}>
      <div className="grid gap-l">

        <Field label="Name">
          <TextInput autoFocus value={name} onChange={setName}
            onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
            placeholder="Project name (or let the system name it later)…"
            surface="high" />
        </Field>
        <Field label="Brief" hint="The goal, scope, and background — shared as context with every agent working on this project's sessions and loops.">
          <TextArea value={brief} onChange={setBrief} rows={4} size="md"
            placeholder="What is this project for? What does success look like?" />
        </Field>
        <Field label="Workspace" hint="Optional — the directory loops + code work in. You can bind or change it later.">
          <div className="flex items-center gap-2">
            {workspaceDir ? (
              <code className="min-w-0 flex-1 truncate rounded-md bg-surface-high px-2.5 py-1.5 font-mono text-[0.8125rem] text-on-surface-var" title={workspaceDir}>{workspaceDir}</code>
            ) : (

              <span className="flex-1 text-on-surface-low text-[0.8125rem] italic">No workspace bound</span>
            )}
            <Button size="sm" variant="secondary" onClick={() => setPickWs(true)}><FolderOpen size={14} /> {workspaceDir ? 'Change' : 'Bind'}</Button>
            {workspaceDir && <IconButton icon={X} label="Clear workspace" size={32} onClick={() => setWorkspaceDir('')} />}
          </div>
        </Field>
        <label className="flex items-center gap-2 text-[0.8125rem] text-on-surface-var cursor-pointer">
          <input type="checkbox" checked={setActive} onChange={(e) => setSetActive(e.target.checked)} className="size-4 accent-primary" />
          Make this the active project (new work defaults here)
        </label>
        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>

          <Button onClick={submit} loading={busy} disabled={!name.trim() || busy}
            disabledReason={!name.trim() ? 'Enter a project name first' : undefined}><Check size={15} /> Create project
          </Button>
        </div>
      </div>
      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)}
          onPick={(dir) => { setPickWs(false); setWorkspaceDir(dir) }} />
      )}
    </Modal>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  const identity = useId()
  const labelId = `${identity}-label`, hintId = `${identity}-hint`
  return <FieldLabelProvider value={labelId}><FieldHintProvider value={hint ? hintId : undefined}>
    <div className="grid gap-s"><span id={labelId} className="text-[0.8125rem] text-on-surface" style={fvs(600)}>{label}</span>
      {hint && <span id={hintId} className="text-[0.75rem] leading-snug text-on-surface-low">{hint}</span>}{children}
    </div>
  </FieldHintProvider></FieldLabelProvider>
}

function ProjectDetailPage({ id, onBack, navigate, query, setQuery }: { id: string; onBack: () => void; navigate: (to: string) => void } & Pick<RouteProps, 'query' | 'setQuery'>) {
  const { project, loading, detailErr, refresh, lists, work, workLoading, adaptersEnabled,
    renaming, setRenaming, nameDraft, setNameDraft, pickWs, setPickWs, regenerating, err, setErr,
    panel, setPanel, active, patch, regenerateContext } = useProjectDetailState(id, query, setQuery)

  if (loading && !project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div></Shell>
  }
  if (!project && detailErr) {
    return <Shell onBack={onBack} title="Project"><LoadError what="project" error={detailErr} onRetry={refresh} /></Shell>
  }
  if (!project) {
    return <Shell onBack={onBack} title="Project"><div className="flex h-full flex-col items-center justify-center gap-3 text-center"><p className="text-on-surface text-[0.9375rem]">This project no longer exists.</p><Button onClick={onBack}><ListChecks size={15} /> Back to projects</Button></div></Shell>
  }

  const launchKind = (kind?: LoopKind) => {
    setActiveProject(id)
    navigate(`loop?project=${encodeURIComponent(id)}` + (kind ? `&kind=${kind}` : ''))
  }
  const launchChat = () => { setActiveProject(id); navigate(`chat?project=${encodeURIComponent(id)}`) }

  const NEW_KINDS: { kind?: LoopKind; label: string; hint: string; icon: LucideIcon }[] = [
    { kind: undefined, label: 'Loop', hint: 'A generic iterative loop', icon: Repeat },
    { kind: 'goal', label: 'Goal', hint: 'Verifiable / open-ended / monitor', icon: Target },
    { kind: 'code', label: 'Code', hint: 'SDLC work in a codebase', icon: Code2 },
    { kind: 'research', label: 'Research', hint: 'Deep web research → report', icon: Telescope },
    { kind: 'design', label: 'Design', hint: 'Design system + tokens', icon: Palette },
  ]

  const workCount = (work?.board ?? []).reduce((n, g) => n + g.count, 0)

  const titleNode = renaming ? (
    <div className="flex items-center gap-2">
      <input autoFocus aria-label="Rename this project" value={nameDraft} onChange={(e) => setNameDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) } else if (e.key === 'Escape') setRenaming(false) }}
        className="min-w-0 rounded-md bg-surface-high px-2.5 py-1 text-on-surface text-[1.0625rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />

      <Button size="sm" ariaLabel="Save the project name" onClick={() => { patch({ name: nameDraft.trim(), name_locked: true }); setRenaming(false) }} disabled={!nameDraft.trim()}
        disabledReason={!nameDraft.trim() ? 'Enter a project name first' : undefined}><Check size={14} /></Button>
      <IconButton icon={X} label="Cancel" size={24} iconSize={15} onClick={() => setRenaming(false)} />
    </div>
  ) : (
    <div className="flex items-center gap-2 min-w-0">
      <FolderKanban size={18} className="shrink-0 text-primary" />

      <PageTitle className="truncate">{project.name}</PageTitle>
      {project.name_locked && <Lock size={12} className="shrink-0 text-on-surface-low" aria-label="Name locked" />}
      {!project.is_builtin && (
        <IconButton icon={Pencil} label="Rename" size={24} iconSize={13} onClick={() => { setNameDraft(project.name); setRenaming(true) }} className="shrink-0 -m-0.5" />
      )}
    </div>
  )
  const headerActions = (
    <>
      <Button variant={active ? 'ghost-accent' : 'ghost'} size="xs" ariaPressed={active}
        onClick={() => { const next = active ? '' : id; setActiveProject(next) }}
        title={active ? 'Active project — new work defaults here' : 'Make this the active project'}>
        <Star size={14} style={active ? { fill: 'var(--color-primary)' } : undefined} /> {active ? 'Active' : 'Set active'}
      </Button>
      <Popover align="right" width={220} placement="bottom"
        trigger={(open, toggle) => (
          <Button size="xs" onClick={toggle} ariaExpanded={open} title="Launch new work scoped under this project">
            <Plus size={14} /> New <ChevronDown size={11} />
          </Button>
        )}>
        {(close) => (
          <div className="flex flex-col gap-0.5">
            {NEW_KINDS.map((k) => (
              <MenuRow key={k.label} icon={<k.icon size={16} />} label={k.label} hint={k.hint}
                onClick={() => { launchKind(k.kind); close() }} />
            ))}
          </div>
        )}
      </Popover>
      <HeaderControl icon={MessageSquare} label="Chat" onClick={launchChat} />

      <HeaderControl icon={Download} label="Export" priority="low"
        hint="Download this project as a portable archive (no credentials)"
        onClick={() => { window.location.href = api.projectExportUrl(id) }} />
    </>
  )

  const panelNode = panel && (
    <SidePanel key={`${panel.kind}:${panel.kind === 'tasks' ? panel.list.id : panel.path}`} fillHeight
      storeKey="project-hub-panel-w" urlKey={{ key: 'panel', setQuery }} onClose={() => setPanel(null)}
      icon={panel.kind === 'tasks' ? <ListChecks size={18} className="text-primary" /> : <FolderTree size={18} className="text-primary" />}
      title={panel.kind === 'tasks' ? panel.list.name : panel.label}>
      {panel.kind === 'tasks'
        ? <TaskListPanel list={panel.list} onOpenTask={(tid) => navigate(`tasks?open=${encodeURIComponent(tid)}`)} />
        : <DirTreePanel path={panel.path} onOpenInFiles={() => navigate(`files?dir=${encodeURIComponent(panel.path)}`)} />}
    </SidePanel>
  )
  return (
    <Shell onBack={onBack} title={project.name} titleNode={titleNode} actions={headerActions} scroll={false} panel={panelNode}>
      <div className="flex h-full min-h-0 flex-col">

        <div className="shrink-0 border-b border-outline-variant/30 px-l py-2.5 flex flex-col gap-2">
          {err && (
            <InlineError onDismiss={() => setErr(null)}>{err}</InlineError>
          )}

          <BriefRow brief={project.brief ?? ''} onSave={(b) => patch({ brief: b })} />

          <div className="flex flex-wrap items-center gap-2 text-[0.75rem]">
            <FolderChip label="Workspace" icon={project.workspace_dir ? FolderOpen : Folder}
              path={project.workspace_dir}
              onPeek={project.workspace_dir ? () => setPanel({ kind: 'dir', label: 'Workspace', path: project.workspace_dir! }) : undefined}
              onBrowse={project.workspace_dir ? () => navigate(`files?dir=${encodeURIComponent(project.workspace_dir!)}`) : undefined}
              onAction={() => setPickWs(true)} actionLabel={project.workspace_dir ? 'Change' : 'Bind'}
              onClear={project.workspace_dir ? () => patch({ workspace_dir: '' }) : undefined}
              emptyText="No workspace bound" />
            <FolderChip label="Context" icon={FolderOpen} path={project.context_dir}
              onPeek={project.context_dir ? () => setPanel({ kind: 'dir', label: 'Context', path: project.context_dir! }) : undefined}
              onBrowse={project.context_dir ? () => navigate(`files?dir=${encodeURIComponent(project.context_dir!)}`) : undefined}
              emptyText="—" title="System-managed — shared context across this project's loops + chats" />

            {adaptersEnabled && project.workspace_dir && (
              <Button variant="ghost" size="xs" onClick={regenerateContext} loading={regenerating}
                title="Rewrite the Gideon context block into this workspace's CLAUDE.md / AGENTS.md / .cursorrules">
                <RefreshCw size={12} /> Refresh context files
              </Button>
            )}
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-px overflow-hidden bg-outline-variant/20"
          style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(280px, 1fr)' }}>

          <HubColumn title={`Work · ${workCount}`}>
            <WorkBoardColumn work={work} loading={workLoading}
              onResume={(runId) => navigate(`loop/${runId}`)} />
          </HubColumn>

          <HubColumn title={`Tasks · ${lists?.length ?? 0} list${(lists?.length ?? 0) === 1 ? '' : 's'}`}>
            {(lists ?? []).length === 0 ? (
              <p className="text-on-surface-low text-[0.8125rem]">No task lists yet — Goal Loop and Code attach their work here when scoped to this project.</p>
            ) : (
              <div className="flex flex-col gap-1">
                {(lists as TaskListItem[]).map((tl) => (
                  <TaskListRow key={tl.id} list={tl} active={panel?.kind === 'tasks' && panel.list.id === tl.id}
                    onOpen={() => setPanel({ kind: 'tasks', list: tl })} />
                ))}
              </div>
            )}
          </HubColumn>
        </div>
      </div>

      {pickWs && (
        <WorkspacePicker mode="brownfield" allowCreate onClose={() => setPickWs(false)}
          onPick={(dir) => { setPickWs(false); patch({ workspace_dir: dir }) }} />
      )}
    </Shell>
  )
}

function HubColumn({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="flex min-h-0 flex-col bg-surface">
    <h2 className="shrink-0 px-l pb-s pt-m text-[0.75rem] uppercase tracking-wide text-on-surface-low">{title}</h2>
    <div className="min-h-0 flex-1 overflow-y-auto px-l pb-l">{children}</div>
  </section>
}
function WorkGroupLabel({ text, count, tone }: { text: string; count: number; tone: 'ok' | 'muted' }) {
  return <header className="flex items-center gap-s text-[0.75rem] uppercase tracking-wide text-on-surface-low">
    {tone === 'ok' && <span className="size-1.5 rounded-full bg-ok" />}<span>{text}</span><span>· {count}</span>
  </header>
}

const WORK_STATE_LABEL: Record<WorkState, string> = {
  needs_input: 'Needs input',
  working: 'Working',
  queued: 'Queued',
  suspended: 'Suspended',
  review: 'Review',
  done: 'Done',
}

export function WorkBoardColumn({ work, loading, onResume }: {
  work: WorkBoard | undefined; loading: boolean; onResume: (runId: string) => void
}) {
  if (!work && loading) return <ListSkeleton rows={4} />
  const groups = work?.board ?? []
  const incomplete = (work?.sections ?? []).filter(section => section.status !== 'ok')
  return <div className="grid gap-m">
    {incomplete.map(section => section.status === 'loading' ? <ListSkeleton key={section.name} rows={2} />
      : <div key={section.name} data-testid={`work-section-error-${section.name}`} className="flex items-start gap-s rounded-lg border border-outline/25 bg-surface-high/60 p-m text-[0.8125rem] text-on-surface-low">
        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
        <span>Couldn't load <span className="text-on-surface-var">{section.name}</span> — the rest of the board is still shown.</span>
      </div>)}
    {!groups.length && !incomplete.length && <p className="text-[0.8125rem] text-on-surface-low">No work here yet — use <span className="text-on-surface-var">New</span> above to launch a loop, or start a chat.</p>}
    {groups.map(group => <section key={group.state} data-testid={`work-group-${group.state}`} className="grid gap-s">
      <WorkGroupLabel text={WORK_STATE_LABEL[group.state]} count={group.count} tone={group.state === 'needs_input' ? 'ok' : 'muted'} />
      {group.rows.map(row => <WorkRowCard key={`${row.origin}:${row.run_id}`} row={row} onResume={() => onResume(row.run_id)} />)}
    </section>)}
  </div>
}
function WorkRowCard({ row, onResume }: { row: WorkRow; onResume: () => void }) {
  const [expanded, expand] = useState(() => !row.collapsed)
  const concealed = row.collapsed && !expanded
  return concealed ? <button type="button" aria-expanded={false} onClick={() => expand(true)}
    className="flex items-center gap-s rounded-lg px-m py-s text-left text-[0.75rem] text-on-surface-low hover:bg-surface-high">
    <ChevronRight size={12} className="shrink-0" /><span className="min-w-0 truncate">{row.title}</span>
  </button> : <article className="flex items-center gap-s rounded-lg border border-outline/20 bg-surface-high/60 px-m py-s text-[0.8125rem] text-on-surface-var">
    <CircleDot size={13} className="shrink-0 text-primary" />
    <span className="min-w-0 flex-1 truncate" title={row.title}>{row.title}</span>
    {row.claim && <span title={`Claimed by ${row.claim.holder}`} className="shrink-0 rounded-md bg-surface-high px-1.5 py-0.5 text-[0.75rem] text-on-surface-low">{row.claim.holder}</span>}
    {row.resumable && <Button variant="ghost" size="xs" onClick={onResume} title="Resume this suspended work"><RefreshCw size={12} /> Resume</Button>}
  </article>
}

function FolderChip({ label, icon: Icon, path, emptyText, title, onPeek, onBrowse, onAction, actionLabel, onClear }: {
  label: string; icon: LucideIcon; path?: string; emptyText: string; title?: string
  onPeek?: () => void; onBrowse?: () => void; onAction?: () => void; actionLabel?: string; onClear?: () => void
}) {
  const pathStyle = 'min-w-0 max-w-[22rem] truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-var'
  const location = !path ? <span className="italic text-on-surface-low">{emptyText}</span>
    : onPeek ? <button type="button" onClick={onPeek} title={`${path} — view contents`} className={`${pathStyle} hover:text-primary`}>{path}</button>
      : <code className={pathStyle} title={path}>{path}</code>
  return <div title={title} className="inline-flex min-w-0 items-center gap-s rounded-lg border border-outline/20 bg-surface-high/50 px-s py-1">
    <Icon size={12} className="shrink-0 text-on-surface-low" /><span className="shrink-0 text-[0.75rem] uppercase tracking-wide text-on-surface-low">{label}</span>
    {location}
    {path && onBrowse && <IconButton icon={FolderOpen} label={`Open ${label} in Files`} title="Open in Files" size={24} iconSize={12} onClick={onBrowse} className="shrink-0 -m-0.5" />}
    {onAction && actionLabel && <Button variant="ghost" size="xs" onClick={onAction} className="shrink-0">{actionLabel}</Button>}
    {onClear && <IconButton icon={X} label={`Unbind ${label}`} title="Unbind" size={20} iconSize={12} onClick={onClear} className="shrink-0" />}
  </div>
}

function TaskListRow({ list, active, onOpen }: { list: TaskListItem; active: boolean; onOpen: () => void }) {
  const totals = list as TaskListItem & { task_count?: number; count?: number }
  const total = totals.task_count ?? totals.count
  const skin = active ? 'bg-surface-high text-on-surface ring-1 ring-primary' : 'bg-surface-high/60 text-on-surface-var hover:bg-surface-high hover:text-on-surface'
  return <button type="button" onClick={onOpen} className={`group flex w-full items-center gap-s rounded-lg px-m py-s text-left text-[0.8125rem] transition-colors ${skin}`}>
    <ListChecks size={13} className="shrink-0 text-on-surface-low" />
    <span className="min-w-0 flex-1 truncate">{list.name}</span>
    {typeof total === 'number' && <span className="shrink-0 text-[0.75rem] tabular-nums text-on-surface-low">{total}</span>}
    <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
  </button>
}

function TaskListPanel({ list, onOpenTask }: { list: TaskListItem; onOpenTask: (taskId: string) => void }) {
  const { data: tasks, loading, error, refresh } = useQuery<TaskItem[]>(`tasklist:tasks:${list.id}`, async () => {
    const result = await api.tasks({ task_list: list.id, limit: 200 })
    return result.tasks
  })
  if (!tasks && error) return <LoadError what="tasks" error={error} onRetry={refresh} />
  if (!tasks && loading) return <ListSkeleton rows={3} what="tasks" />
  if (!tasks?.length) return <p className="text-on-surface-low text-[0.8125rem]">No tasks in this list yet.</p>
  return <ul className="grid gap-s">
    {tasks.map(t => {
      const sm = statusMeta(t.status)
      return <li key={t.id}>
        <button type="button" onClick={() => onOpenTask(t.id)} className="group flex w-full items-start gap-s rounded-lg border border-outline/20 px-m py-s text-left text-[0.8125rem] hover:bg-surface-high">
          <sm.icon size={14} className="mt-0.5 shrink-0" style={{ color: sm.tone }} role="img" aria-label={sm.label} />
          <span className={`min-w-0 flex-1 ${TERMINAL.has(t.status) ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{t.title}</span>
          <ChevronRight size={14} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
        </button>
      </li>
    })}
  </ul>
}

function DirTreePanel({ path, onOpenInFiles }: { path: string; onOpenInFiles: () => void }) {
  const { cur, setCur, data, loading, error, crumbs, entries, ordered } = useProjectDirectory(path)
  return (
    <div className="flex flex-col gap-2">
      <Button size="sm" variant="ghost" onClick={onOpenInFiles}><FolderOpen size={14} /> Open in Files</Button>

      <div className="flex flex-wrap items-center gap-0.5 text-[0.75rem] text-on-surface-low">
        <button type="button" onClick={() => setCur(path)} className={`truncate rounded px-1 py-0.5 hover:bg-surface-high ${cur === path ? 'text-on-surface' : 'hover:text-on-surface'}`}>{path.split('/').pop() || path}</button>
        {crumbs.map((seg, i) => (
          <span key={i} className="inline-flex items-center gap-0.5">
            <ChevronRight size={11} className="shrink-0" />
            <button type="button" onClick={() => setCur(path + '/' + crumbs.slice(0, i + 1).join('/'))}
              className={`truncate rounded px-1 py-0.5 hover:bg-surface-high ${i === crumbs.length - 1 ? 'text-on-surface' : 'hover:text-on-surface'}`}>{seg}</button>
          </span>
        ))}
      </div>
      {loading && !data ? <div className="flex justify-center py-l"><Loader2 size={16} className="animate-spin text-on-surface-low" /></div>
        : error ? <p className="text-on-surface-low text-[0.8125rem]">{projectDirectoryError(error)}</p>
        : entries.length === 0 ? <p className="text-on-surface-low text-[0.8125rem]">This directory is empty.</p>
        : (
          <ul className="flex flex-col gap-0.5">
            {ordered.map(entry => <DirEntryRow key={entry.path} entry={entry} onClick={entry.is_dir ? () => setCur(entry.path) : onOpenInFiles} />)}
          </ul>
        )}
    </div>
  )
}

function DirEntryRow({ entry, onClick }: { entry: FsEntry; onClick: () => void }) {
  const Glyph = entry.is_dir ? Folder : FileIcon
  return <li><button type="button" onClick={onClick} className="group flex w-full items-center gap-s rounded-lg px-m py-s text-left text-[0.8125rem] text-on-surface-var hover:bg-surface-high hover:text-on-surface">
    <Glyph size={14} className={`shrink-0 ${entry.is_dir ? 'text-primary' : 'text-on-surface-low'}`} />
    <span className="min-w-0 flex-1 truncate">{entry.name}</span>
    {entry.is_dir && <ChevronRight size={14} className="shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />}
  </button></li>
}

function BriefRow({ brief, onSave }: { brief: string; onSave: (b: string) => void }) {
  const [state, update] = useReducer((state: { editing: boolean; draft: string }, patch: Partial<typeof state>) => ({ ...state, ...patch }), { editing: false, draft: brief })
  useEffect(() => { update({ draft: brief }) }, [brief])
  const cancel = () => update({ editing: false, draft: brief })
  const save = () => { onSave(state.draft.trim()); update({ editing: false }) }
  if (state.editing) return <div className="grid gap-s" onKeyDown={event => { if (event.target instanceof HTMLTextAreaElement && event.key === 'Escape') cancel() }}>
    <TextArea autoFocus value={state.draft} onChange={draft => update({ draft })} rows={3} ariaLabel="Project brief"
      placeholder="Project brief — the goal, scope, and background. Shared as context with every agent working on this project's sessions and loops." />
    <div className="flex items-center gap-s"><Button size="sm" onClick={save}><Check size={13} /> Save brief</Button>
      <Button variant="ghost" size="xs" onClick={cancel}>Cancel</Button></div>
  </div>
  return <button type="button" onClick={() => update({ editing: true })} title="Edit project brief"
    className="group flex items-start gap-s rounded-lg px-s py-1 text-left hover:bg-surface-high/50">
    <FileText size={13} className="mt-0.5 shrink-0 text-on-surface-low" />
    <span className={`min-w-0 flex-1 text-[0.8125rem] ${brief ? 'text-on-surface-var line-clamp-2' : 'text-on-surface-low italic'}`}>{brief || 'Add a project brief — shared as context with every agent working here.'}</span>
    <Pencil size={12} className="mt-0.5 shrink-0 text-on-surface-low opacity-0 group-hover:opacity-100 focus-within:opacity-100" />
  </button>
}

function Shell({ title, titleNode, onBack, actions, scroll = true, panel, children }: {
  title: string; titleNode?: React.ReactNode; onBack: () => void
  actions?: React.ReactNode; scroll?: boolean

  panel?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        keepCornerPadding

        left={titleNode ?? <div className="flex items-center gap-2"><FolderKanban size={18} className="text-primary" /><PageTitle>{title}</PageTitle></div>}
        right={<div className="flex items-center gap-1.5">{actions}<HeaderActions><HeaderControl icon={ListChecks} label="All projects" onClick={onBack} priority="primary" /></HeaderActions></div>} />
      <div className="flex min-h-0 flex-1">

        <div className={`min-w-0 flex-1 ${scroll ? 'overflow-y-auto' : 'overflow-hidden'}`}>{children}</div>
        <AnimatePresence>{panel}</AnimatePresence>
      </div>
    </div>
  )
}
