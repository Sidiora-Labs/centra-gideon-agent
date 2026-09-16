import { useState, useEffect, useRef } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { PageTitle } from '../../shared/ui/PageTitle'
import { api, type TaskItem } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { TaskForm, emptyDraft, draftToPayload, type TaskDraft } from './TaskForm'
import { useTaskOperation } from './taskEditorState'

export function TaskCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: (t: TaskItem) => void }) {
  const [draft, setDraft] = useState<TaskDraft>(emptyDraft)
  const { busy: saving, error: err, setError, run } = useTaskOperation('create-task')
  const { data: allTasks = [] } = useQuery<TaskItem[]>('tasks-all', () => api.tasks().then(result => result.tasks).catch(() => []), { persist: true })
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])
  const create = () => {
    if (!draft.title.trim()) { setError('Title is required'); return }
    void run(() => api.createTask(draftToPayload(draft)), task => { invalidateKeys('tasks', true); onCreated(task) }, 'Create failed')
  }
  return <div className="flex h-full flex-col bg-surface">
    <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>New task</PageTitle></div>} />
    <div role="region" aria-label="Task editor" tabIndex={0} className="min-h-0 flex-1 overflow-y-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary">
      <div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
        <TaskForm draft={draft} onChange={setDraft} allTasks={allTasks} />
        {err && <p ref={errRef} role="alert" data-type="body-s" className="mt-l rounded-md border-l-2 border-danger bg-danger/10 p-m text-danger">{err}</p>}
      </div>
    </div>
    <footer className="shrink-0 border-t border-outline-variant/40 bg-surface-container/30 px-l py-m"><div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
      <Button variant="ghost" onClick={onBack}>Cancel</Button>
      <Button onClick={create} loading={saving} loadingLabel="Creating…" disabled={saving || !draft.title.trim()} disabledReason={!draft.title.trim() ? 'Enter a task title first' : undefined}><Check size={16} /> Create task</Button>
    </div></footer>
  </div>
}
