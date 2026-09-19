import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { TaskItem } from '../../shared/data/api'
import { TaskGraph } from './TaskGraph'
import { filterTasksByTag, taskNoMatchCause, taskTagOptions } from './taskGraphState'

const tasks = [
  { id: 'plan', title: 'Plan release', status: 'open', priority: 'high', labels: [' release ', 'planning'] },
  { id: 'ship', title: 'Ship release', status: 'blocked', priority: 'medium', labels: ['release'], dependencies: [{ depends_on_task_id: 'plan', dependency_type: 'BLOCKS' }] },
] as TaskItem[]

describe('task graph accessibility and filters', () => {
  beforeAll(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
  })
  afterAll(() => vi.unstubAllGlobals())

  it('uses named native buttons for graph nodes and activates them from the keyboard', async () => {
    const open = vi.fn()
    render(<TaskGraph tasks={tasks} onOpen={open} />)

    const node = screen.getByRole('button', { name: 'Open task: Plan release' })
    node.focus()
    await userEvent.keyboard('{Enter}')

    expect(open).toHaveBeenCalledWith('plan')
  })

  it('derives reachable tag choices and applies the selected tag to real task rows', () => {
    expect(taskTagOptions(tasks)).toEqual([
      { key: 'planning', label: 'planning', count: 1 },
      { key: 'release', label: 'release', count: 2 },
    ])
    expect(filterTasksByTag(tasks, 'planning').map(task => task.id)).toEqual(['plan'])
    expect(filterTasksByTag(tasks, 'release').map(task => task.id)).toEqual(['plan', 'ship'])
  })

  it('blames search only when search itself returned no matches', () => {
    expect(taskNoMatchCause({ query: 'release', searchMatches: 0, scope: 'Backend', tag: '', status: 'all', list: '', mine: false })).toBe('search')
    expect(taskNoMatchCause({ query: 'release', searchMatches: 2, scope: 'Backend', tag: '', status: 'all', list: '', mine: false })).toBe('filters')
    expect(taskNoMatchCause({ query: '', searchMatches: null, scope: 'Backend', tag: '', status: 'all', list: '', mine: false })).toBe('scope')
    expect(taskNoMatchCause({ query: '', searchMatches: null, scope: '', tag: 'release', status: 'all', list: '', mine: false })).toBe('tag')
  })
})
