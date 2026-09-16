import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type TaskItem, type ExitCriterion, type ActionPlanItem, type ProjectItem, type TaskListItem } from '../../shared/data/api'
import { getActiveProject } from '../../shared/data/activeProject'
import { STATUSES, PRIORITIES } from './taskMeta'
import { Field, TextInput, TextArea, DateInput, Segmented, ChipInput, Select } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { ChecklistEditor, DependencyEditor, NotesEditor } from './formControls'
import { chooseTaskProject, useTaskOperation, type TaskDraft } from './taskEditorState'
export { emptyDraft, toDraft, draftToPayload, type TaskDraft } from './taskEditorState'

const noteFields = [
  { key: 'notes', label: 'General', hint: 'Free notes on this task.', placeholder: 'Add a note, Enter' },
  { key: 'research_notes', label: 'Research', hint: 'Findings gathered while investigating.', placeholder: 'Add a research note, Enter' },
  { key: 'execution_notes', label: 'Execution', hint: 'What was done while carrying the task out.', placeholder: 'Add an execution note, Enter' },
] as const

export function TaskForm({ draft, onChange, compact, allTasks = [] }: { draft: TaskDraft; onChange: (d: TaskDraft) => void; compact?: boolean; allTasks?: TaskItem[] }) {
  const patch = (change: Partial<TaskDraft>) => onChange({ ...draft, ...change })
  const set = <K extends keyof TaskDraft>(key: K, value: TaskDraft[K]) => patch({ [key]: value })
  return <div className={`grid ${compact ? 'gap-l' : 'gap-xl'}`}>
    <EditorSection title="Basics" compact={compact}>
      <Field label="Title"><TextInput required autoFocus value={draft.title} onChange={value => set('title', value)} placeholder="What needs to happen?" /></Field>
      <Field label="Description"><TextArea value={draft.description ?? ''} onChange={value => set('description', value)} placeholder="Context, acceptance notes, links… (markdown)" rows={compact ? 4 : 6} /></Field>
    </EditorSection>
    <EditorSection title="Classification" compact={compact}>
      <Field label="Status"><Segmented options={STATUSES.map(status => ({ key: status.key, label: status.label, tone: status.tone, icon: status.icon }))} value={draft.status ?? 'open'} onChange={value => set('status', value)} /></Field>
      <Field label="Priority"><Segmented options={PRIORITIES.map(priority => ({ key: priority.key, label: priority.label, tone: priority.tone }))} value={draft.priority ?? 'medium'} onChange={value => set('priority', value)} /></Field>
      <div className="grid grid-cols-2 gap-m">
        <ProjectListPicker key={draft.id ?? 'new'} taskListId={draft.task_list_id ?? ''} onSelection={(projectId, taskListId) => patch({ task_list_id: taskListId, ...(!draft.id ? { project_id: projectId } : {}) })} />
        <Field label="Assignee"><TextInput value={draft.assignee ?? ''} onChange={value => set('assignee', value)} placeholder="Who owns it" /></Field>
        <Field label="Due"><DateInput value={draft.due ?? ''} onChange={value => set('due', value)} /></Field>
      </div>
      <Field label="Tags"><ChipInput values={draft.labels ?? []} onChange={value => set('labels', value)} placeholder="Add a tag, Enter" max={10} /></Field>
    </EditorSection>
    <EditorSection title="Structure" compact={compact}>
      <Field label="Depends on" hint="Prerequisite tasks that must finish first. Choices that would form a cycle are blocked."><DependencyEditor selfId={draft.id} allTasks={allTasks} value={draft.depends_on ?? []} onChange={value => set('depends_on', value)} /></Field>
      <Field label="Exit criteria" hint="Conditions that must be true for this task to count as done."><ChecklistEditor<ExitCriterion> items={draft.exit_criteria ?? []} onChange={value => set('exit_criteria', value)} doneKey="met" placeholder="Add a completion condition" /></Field>
      <Field label="Action plan" hint="Ordered steps to carry the task out."><ChecklistEditor<ActionPlanItem> items={draft.action_plan ?? []} onChange={value => set('action_plan', value)} doneKey="completed" placeholder="Add a step" ordered /></Field>
    </EditorSection>
    <EditorSection title="Notes" compact={compact}>
      {noteFields.map(field => <Field key={field.key} label={field.label} hint={field.hint}><NotesEditor items={draft[field.key] ?? []} onChange={value => set(field.key, value)} placeholder={field.placeholder} /></Field>)}
    </EditorSection>
    <EditorSection title="Agent instructions" compact={compact}>
      <Field label="Template" hint="Guidance an agent follows when it picks up this task."><TextArea value={draft.agent_instructions_template ?? ''} onChange={value => set('agent_instructions_template', value)} placeholder="e.g. Investigate, propose a fix, open a PR…" rows={compact ? 3 : 4} mono /></Field>
    </EditorSection>
  </div>
}

function EditorSection({ title, compact, children }: { title: string; compact?: boolean; children: ReactNode }) {
  return <section className={`grid rounded-lg border border-outline-variant/30 bg-surface-container/20 ${compact ? 'gap-m p-m' : 'gap-l p-l'}`}>
    <h2 className="border-l-2 border-primary pl-s text-[0.8125rem] font-medium text-on-surface">{title}</h2>
    {children}
  </section>
}

function ProjectListPicker({ taskListId, onSelection }: { taskListId: string; onSelection: (projectId: string, taskListId: string) => void }) {
  const [catalog, setCatalog] = useState<{ projects: ProjectItem[]; lists: TaskListItem[] }>({ projects: [], lists: [] })
  const [projectId, setProjectId] = useState('')
  const [creating, setCreating] = useState<'project' | 'list' | null>(null)
  const [name, setName] = useState('')
  const latest = useRef({ taskListId, onSelection })
  latest.current = { taskListId, onSelection }
  const { busy, error, setError, run } = useTaskOperation('project-list-picker')
  useEffect(() => {
    let active = true
    Promise.all([api.projects(), api.taskLists()]).then(([projects, lists]) => {
      if (!active) return
      setCatalog({ projects, lists })
      const selected = chooseTaskProject(projects, lists, latest.current.taskListId, getActiveProject())
      setProjectId(selected)
      latest.current.onSelection(selected, latest.current.taskListId)
    }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : 'Could not load projects') })
    return () => { active = false }
  }, [])
  const close = () => { setCreating(null); setName(''); setError('') }
  const select = (project: string, list: string) => { setProjectId(project); latest.current.onSelection(project, list) }
  const create = () => {
    const trimmed = name.trim()
    if (!trimmed || busy || !creating || (creating === 'list' && !projectId)) return
    if (creating === 'project') {
      void run(() => api.createProject({ name: trimmed }), project => {
        setCatalog(current => ({ ...current, projects: [...current.projects, project] }))
        select(project.id, ''); close()
      }, 'Could not create project')
    } else {
      void run(() => api.createTaskList({ name: trimmed, project_id: projectId }), list => {
        setCatalog(current => ({ ...current, lists: [...current.lists, list] }))
        select(list.project_id, list.id); close()
      }, 'Could not create task list')
    }
  }
  const open = (kind: 'project' | 'list') => { setCreating(kind); setName(''); setError('') }
  return <>
    {creating ? <div className="col-span-2"><Field label={creating === 'project' ? 'New project' : 'New task list'}>
      <div className="flex items-center gap-s">
        <TextInput autoFocus value={name} onChange={setName} placeholder={creating === 'project' ? 'Project name' : 'Task list name'} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); create() } }} />
        <Button size="md" loading={busy} disabled={busy || !name.trim()} disabledReason={!name.trim() ? 'Enter a name first' : undefined} onClick={create}>Add</Button>
        <Button variant="ghost" size="md" onClick={close}>Cancel</Button>
      </div>
    </Field></div> : <>
      <Field label="Project"><Select value={projectId} onChange={id => id === '__new__' ? open('project') : select(id, '')} options={[...catalog.projects.map(project => ({ value: project.id, label: project.is_builtin ? `${project.name} (builtin)` : project.name })), { value: '__new__', label: '＋ New project…' }]} /></Field>
      <Field label="Task list"><Select value={taskListId} disabled={!projectId} onChange={id => id === '__new__' ? open('list') : select(projectId, id)} options={[{ value: '', label: '(none)' }, ...catalog.lists.filter(list => list.project_id === projectId).map(list => ({ value: list.id, label: list.name })), ...(projectId ? [{ value: '__new__', label: '＋ New task list…' }] : [])]} /></Field>
    </>}
    {error && <p role="alert" data-type="caption" className="col-span-2 text-danger">{error}</p>}
  </>
}
