import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

describe('api.allTasks', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('loads every page and returns the task envelope consumed by task views', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      calls.push(url)
      const offset = new URL(url, 'http://localhost').searchParams.get('offset')
      const body = offset === '2'
        ? { tasks: [{ id: 'three' }], total: 3, owner: 'later-owner' }
        : { tasks: [{ id: 'one' }, { id: 'two' }], total: 3, owner: 'task-owner' }
      return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } })
    }))

    const result = await api.allTasks({ task_list: 'list one', status: 'ready', mine: true })

    expect(result.tasks.map(task => task.id)).toEqual(['one', 'two', 'three'])
    expect(result).toMatchObject({ total: 3, owner: 'task-owner' })
    expect(calls).toEqual([
      '/api/tasks?task_list=list+one&status=ready&limit=500&mine=1',
      '/api/tasks?task_list=list+one&status=ready&limit=500&offset=2&mine=1',
    ])
  })
})
