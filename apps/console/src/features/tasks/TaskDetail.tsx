import { useEffect, useState, type ReactNode } from 'react'
import { Pencil, Trash2, Check, X, ExternalLink, Lock, CornerDownRight, Send, AlertTriangle, FolderKanban } from 'lucide-react'
import { rowSubject } from '../../shared/data/rowSubject'
import { api, type TaskItem, type TaskNote } from '../../shared/data/api'
import { FieldError } from '../../shared/ui/forms'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { FormFooter } from '../../shared/ui/FormFooter'
import { TextLink } from '../../shared/ui/TextLink'
import { Meter } from '../../shared/ui/Meter'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { Markdown } from '../../shared/ui/Markdown'
import { TodoList, type TodoItem } from '../../shared/vendor/assistant-ui/elements/todo-list'
import { confirm, confirmDelete } from '../../shared/ui/dialog'
import { accentChip } from '../../shared/theme/accent'
import { statusMeta, priorityMeta, dueMeta, relTime, isExitComplete, exitDoneCount, blockKindMeta } from './taskMeta'
import { prereqIds } from './dag'
import { TaskForm, toDraft, draftToPayload, type TaskDraft } from './TaskForm'
import { taskChecklistPatch, useTaskOperation, useTaskCommentThread } from './taskEditorState'

const relationStyle = 'flex min-h-9 items-center gap-s rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-left transition-colors enabled:hover:bg-surface-high disabled:cursor-default'
const chipStyle = 'inline-flex h-7 items-center gap-1.5 rounded-md px-m'

export function TaskDetail({ task, onSaved, onDeleted, editing: editingProp, onEditingChange, allTasks = [], onOpenTask }: {
  task: TaskItem; onSaved: (t: TaskItem) => void; onDeleted: () => void; editing: boolean; onEditingChange: (v: boolean) => void; allTasks?: TaskItem[]; onOpenTask?: (id: string) => void
}) {
  const readOnly = task.provider === 'project'
  const [draft, setDraft] = useState<TaskDraft>(() => toDraft(task))
  const { busy, error, setError, run } = useTaskOperation(task.id)
  useEffect(() => { setDraft(toDraft(task)) }, [task.id])
  const dependents = allTasks.filter(candidate => prereqIds(candidate).includes(task.id))
  const save = () => {
    if (!draft.title.trim()) { setError('Title is required'); return }
    void run(() => api.updateTask(task.id, draftToPayload(draft)), updated => { onSaved(updated); onEditingChange(false) }, 'Save failed')
  }
  const remove = () => void run(async () => {
    const count = dependents.length
    const consequence = count ? ` ${count} task${count === 1 ? '' : 's'} waiting on it ${count === 1 ? 'becomes' : 'become'} unblocked.` : ''
    if (!await confirm({ title: `Delete "${task.title}"?`, body: `This cannot be undone.${consequence}`, danger: true, confirmLabel: 'Delete' })) return false
    await api.deleteTask(task.id, task.provider)
    return true
  }, removed => { if (removed) onDeleted() }, 'Delete failed')
  const toggle = (kind: 'exit' | 'step', index: number) => {
    if (readOnly) return
    void run(() => api.updateTask(task.id, taskChecklistPatch(task, kind, index)), onSaved, kind === 'exit' ? 'Could not update exit criteria' : 'Could not update action plan')
  }
  if (editingProp && !readOnly) return <div className="grid gap-l">
    <TaskForm draft={draft} onChange={setDraft} compact allTasks={allTasks} />
    {error && <FieldError>{error}</FieldError>}
    <FormFooter>
      <Button size="sm" variant="ghost" onClick={() => { setDraft(toDraft(task)); setError(''); onEditingChange(false) }}><X size={15} /> Cancel</Button>
      <Button size="sm" onClick={save} loading={busy} disabled={busy || !draft.title.trim()} disabledReason={!draft.title.trim() ? 'Enter a task title first' : undefined}><Check size={15} /> Save</Button>
    </FormFooter>
  </div>
  const sm = statusMeta(task.status)
  const pm = priorityMeta(task.priority)
  const due = dueMeta(task.due)
  const criteria = task.exit_criteria ?? []
  const steps = task.action_plan ?? []
  const prerequisites = prereqIds(task)
  const taskIndex = new Map(allTasks.map(entry => [entry.id, entry]))
  const blockKind = task.status === 'blocked' ? blockKindMeta(task.blocked_reason_kind) : null
  return <div className="grid gap-l">
    <div className="flex flex-wrap items-center gap-s border-b border-outline-variant/30 pb-m">
      {readOnly ? <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Lock size={13} /> Managed by project — read-only</span> : <>
        <Button size="sm" variant="secondary" onClick={() => { setDraft(toDraft(task)); onEditingChange(true) }}><Pencil size={14} /> Edit</Button>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy}><Trash2 size={14} /> Delete</Button>
      </>}
      {task.url && <TextLink href={task.url} external icon={ExternalLink} size="sm" className="ml-auto">Open</TextLink>}
      <span className={task.url ? '' : 'ml-auto'}><InvestigateButton kind="task" id={task.id} backLink="#/tasks" /></span>
    </div>
    {error && <FieldError>{error}</FieldError>}
    <div className="flex flex-wrap gap-s">
      <span data-type="body-s" className={chipStyle} style={{ color: sm.tone, background: `color-mix(in srgb, ${sm.tone} 18%, transparent)` }}><sm.icon size={13} /> {sm.label}</span>
      <span data-type="body-s" className={chipStyle} style={{ color: pm.tone, background: `color-mix(in srgb, ${pm.tone} 16%, transparent)` }}>{pm.label}</span>
      {task.project && <span data-type="body-s" className={chipStyle} style={accentChip}><FolderKanban size={13} /> {task.project}</span>}
      {task.assignee && <span data-type="body-s" className={`${chipStyle} bg-surface-high text-on-surface-var`}>@{task.assignee}</span>}
      {due && <span data-type="body-s" className={chipStyle} style={{ color: due.tone, background: `color-mix(in srgb, ${due.tone} 14%, transparent)` }}>{due.label}</span>}
    </div>
    {!!task.labels?.length && <div className="flex flex-wrap gap-1.5">{task.labels.map(label => <span key={label} data-type="caption" className="rounded-md border border-outline-variant/30 px-2 py-1 text-on-surface-var">{label}</span>)}</div>}
    {(blockKind || task.block_reason?.is_blocked) && <div data-type="body-s" className="rounded-md border-l-2 border-warn bg-warn/10 p-m">
      <div className="mb-1 flex items-center gap-1.5 font-medium text-warn"><AlertTriangle size={14} />{blockKind?.label ?? 'Blocked'}</div>
      <div className="text-on-surface">{task.block_reason?.message || (task.block_reason?.blocking_task_titles?.length ? `Waiting on ${task.block_reason.blocking_task_titles.join(', ')}` : blockKind?.hint)}</div>
    </div>}
    {task.description && <DetailSection label="Description"><Markdown>{task.description}</Markdown></DetailSection>}
    {criteria.length > 0 && <DetailSection label={`Exit criteria · ${exitDoneCount(criteria)}/${criteria.length}`}>
      <Meter label="Exit criteria" pct={exitDoneCount(criteria) / criteria.length * 100} tone="var(--color-ok)" className="mb-2" />
      <ul className="grid gap-xs">{criteria.map((criterion, index) => {
        const done = isExitComplete(criterion)
        return <li key={index} data-type="body-s" className="flex items-start gap-s">
          <button type="button" disabled={readOnly || busy} onClick={() => toggle('exit', index)} aria-label={done ? 'Mark criterion incomplete' : 'Mark criterion complete'} className="group -mx-1 shrink-0 inline-flex size-6 items-center justify-center disabled:cursor-default">
            <span className="inline-flex size-4 items-center justify-center rounded-sm transition-shadow group-hover:ring-2 group-hover:ring-ok/40 group-disabled:ring-0" style={{ background: done ? 'var(--color-ok)' : 'var(--color-surface-high)' }}>{done && <Check size={11} className="text-white" />}</span>
          </button>
          <span className={done ? 'text-on-surface-low line-through' : 'text-on-surface'}>{criterion.description}</span>
        </li>
      })}</ul>
    </DetailSection>}
    {steps.length > 0 && <DetailSection label="Action plan"><TodoList heading="" className="max-w-none"
      items={steps.map((step, index): TodoItem => ({ id: `${task.id}:${index}`, text: step.content ?? step.description ?? '', status: step.completed ? 'done' : 'pending' }))}
      onToggle={!readOnly && !busy ? (_item, index) => toggle('step', index) : undefined} />
    </DetailSection>}
    {prerequisites.length > 0 && <DetailSection label={`Depends on · ${prerequisites.length}`}><div className="grid gap-1.5">
      {prerequisites.map(id => {
        const related = taskIndex.get(id)
        return <button key={id} type="button" disabled={!related || !onOpenTask} onClick={() => onOpenTask?.(id)} className={relationStyle}><RelatedTask task={related} fallback={id} /></button>
      })}
      {task.block_reason?.is_blocked && <p data-type="caption" className="mt-0.5 flex items-center gap-1.5 text-warn"><AlertTriangle size={12} />{task.block_reason.message || 'Has unfinished prerequisites'}</p>}
    </div></DetailSection>}
    {dependents.length > 0 && <DetailSection label={`Blocks · ${dependents.length}`}><div className="grid gap-1.5">{dependents.map(related => <button key={related.id} type="button" disabled={!onOpenTask} onClick={() => onOpenTask?.(related.id)} className={relationStyle}><RelatedTask task={related} fallback={related.id} /></button>)}</div></DetailSection>}
    <NoteChannel label="Notes" notes={task.notes} /><NoteChannel label="Research notes" notes={task.research_notes} /><NoteChannel label="Execution notes" notes={task.execution_notes} />
    {task.agent_instructions_template && <DetailSection label="Agent instructions"><pre data-type="body-s" className="overflow-x-auto rounded-md border border-outline-variant/30 bg-surface-container/40 p-m font-mono whitespace-pre-wrap text-on-surface-var">{task.agent_instructions_template}</pre></DetailSection>}
    <Comments taskId={task.id} provider={task.provider} />
    <p data-type="caption" className="text-on-surface-low">{task.created_at && <>Created {relTime(task.created_at)}</>}{task.updated_at && task.updated_at !== task.created_at && <> · updated {relTime(task.updated_at)}</>}</p>
  </div>
}

function RelatedTask({ task, fallback }: { task?: TaskItem; fallback: string }) {
  const status = statusMeta(task?.status)
  const complete = task?.status === 'done'
  return <><status.icon size={14} style={{ color: status.tone }} className="shrink-0" /><span data-type="body-s" className={`min-w-0 flex-1 truncate ${complete ? 'text-on-surface-low line-through' : 'text-on-surface'}`}>{task?.title ?? fallback}</span>{task && !complete && <span data-type="caption" className="text-on-surface-low">{status.label}</span>}</>
}
function DetailSection({ label, children }: { label: string; children: ReactNode }) {
  return <section className="grid gap-s"><h2 data-type="caption" className="border-l-2 border-primary/50 pl-s text-on-surface-low uppercase tracking-wide">{label}</h2><div>{children}</div></section>
}
function NoteChannel({ label, notes }: { label: string; notes?: TaskNote[] }) {
  const visible = notes?.filter(note => note.content?.trim()) ?? []
  if (!visible.length) return null
  return <DetailSection label={`${label} · ${visible.length}`}><ul className="grid gap-s">{visible.map((note, index) => <li key={index} className="rounded-md border border-outline-variant/25 bg-surface-container/40 p-m"><p data-type="body-s" className="whitespace-pre-wrap text-on-surface">{note.content}</p>{(note.timestamp || note.created_at) && <p data-type="caption" className="mt-1 text-on-surface-low">{relTime(note.timestamp || note.created_at)}</p>}</li>)}</ul></DetailSection>
}
function Comments({ taskId, provider }: { taskId: string; provider?: string }) {
  const thread = useTaskCommentThread(taskId, provider)
  const remove = async (id: string, body: string) => { if (await confirmDelete('comment', rowSubject([body], 40))) await thread.remove(id) }
  return <DetailSection label={`Comments${thread.comments?.length ? ` · ${thread.comments.length}` : ''}`}><div className="grid gap-s">
    {thread.comments?.map(comment => <article key={comment.id} className="group flex gap-s"><CornerDownRight size={14} className="mt-1 shrink-0 text-on-surface-low" /><div className="min-w-0 flex-1 rounded-md border border-outline-variant/25 bg-surface-container/40 p-m">
      <div data-type="caption" className="mb-1 flex items-center gap-s text-on-surface-low"><span className="text-on-surface-var">{comment.author || 'you'}</span><span>{relTime(comment.created_at)}</span><IconButton icon={Trash2} label="Delete comment" size={24} iconSize={13} tone="danger" onClick={() => remove(comment.id, comment.body)} className="ml-auto opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100" /></div>
      <p data-type="body-s" className="whitespace-pre-wrap break-words text-on-surface">{comment.body}</p>
    </div></article>)}
    <div className="flex items-end gap-s"><textarea aria-label="Comment" rows={1} value={thread.draft} onChange={event => thread.setDraft(event.target.value)} placeholder="Add a comment…" onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void thread.send() } }} data-type="body-s" className="min-w-0 flex-1 resize-none rounded-md border border-outline-variant/30 bg-surface p-m text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
      <button type="button" aria-label="Post comment" onClick={thread.send} {...unavailableWhen(!thread.draft.trim(), 'Write a comment first', { busy: thread.sending })} className="inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-on-primary disabled:opacity-40 aria-disabled:cursor-not-allowed aria-disabled:opacity-40"><Send size={15} /></button>
    </div>
  </div></DetailSection>
}
