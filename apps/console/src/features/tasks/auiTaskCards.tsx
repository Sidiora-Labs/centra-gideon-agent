import { useState } from 'react'
import { TaskCard, type TaskCardState } from '../../shared/vendor/assistant-ui/elements/task-card'
import { TodoList, type TodoItem } from '../../shared/vendor/assistant-ui/elements/todo-list'
import { api, type TaskItem } from '../../shared/data/api'

export function taskCardState(status: string): TaskCardState {
  if (status === 'done' || status === 'completed') return 'done'
  if (status === 'in_progress' || status === 'active') return 'working'
  if (status === 'failed') return 'failed'
  if (status === 'cancelled') return 'cancelled'
  return 'waiting'
}

export function TaskTodoResult({ task }: { task: TaskItem }) {
  const items: TodoItem[] = (task.action_plan ?? []).map((step, index) => ({
    id: `${task.id}:${step.sequence ?? index}`, text: step.content ?? step.description ?? '',
    status: step.completed ? 'done' : 'pending',
  })).filter(item => item.text)
  return items.length ? <TodoList items={items} aria-label={`Action plan for ${task.title}`} /> : null
}

export function GideonTaskCard({ task, onSaved, onOpen }: {
  task: TaskItem; onSaved: (task: TaskItem) => void; onOpen?: (id: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function complete() {
    setBusy(true)
    setError('')
    try {
      const saved = await api.updateTask(task.id, { status: 'done' })
      if (saved.id !== task.id || saved.status !== 'done') throw new Error('Task completion was not confirmed')
      onSaved(saved)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return <div aria-label={`Task ${task.id}`}>
    <TaskCard label={task.title} meta={task.id} state={taskCardState(task.status)}
      result={task.description || undefined}
      actions={(onOpen || (task.status !== 'done' && task.status !== 'completed' && task.provider !== 'project')) && <div className="flex gap-2">
        {onOpen && <button type="button" onClick={() => onOpen(task.id)}>Open task</button>}
        {task.status !== 'done' && task.status !== 'completed' && task.provider !== 'project' &&
          <button type="button" disabled={busy} onClick={() => void complete()}>Complete task</button>}
      </div>} />
    {error && <p role="alert">{error}</p>}
  </div>
}
