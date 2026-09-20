type TaskListCreated = { projectId: string }
type Listener = (event: TaskListCreated) => void

const listeners = new Set<Listener>()

export function emitTaskListCreated(projectId: string): void {
  for (const listener of [...listeners]) listener({ projectId })
}

export function onTaskListCreated(listener: Listener): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
