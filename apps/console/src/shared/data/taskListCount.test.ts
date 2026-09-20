import { describe, expect, it, vi } from 'vitest'
import { api } from './api'
import { emitTaskListCreated, onTaskListCreated } from './taskListCount'

describe('task-list count emitter', () => {
  it('updates subscribers immediately and can unsubscribe without fetching', () => {
    const listener = vi.fn()
    const unsubscribe = onTaskListCreated(listener)

    emitTaskListCreated('p-1')
    unsubscribe()
    emitTaskListCreated('p-2')

    expect(listener).toHaveBeenCalledOnce()
    expect(listener).toHaveBeenCalledWith({ projectId: 'p-1' })
  })

  it('emits the owning project after task-list creation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 'tl-1', name: 'Plan', project_id: 'p-1' }), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    })))
    const listener = vi.fn()
    const unsubscribe = onTaskListCreated(listener)

    await api.createTaskList({ name: 'Plan', project_id: 'p-1' })

    expect(listener).toHaveBeenCalledWith({ projectId: 'p-1' })
    unsubscribe()
    vi.unstubAllGlobals()
  })
})
