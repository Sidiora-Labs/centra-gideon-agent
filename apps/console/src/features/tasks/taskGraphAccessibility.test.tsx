import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { TaskItem } from '../../shared/data/api'
import { scopedGraphMetrics, TaskGraph } from './TaskGraph'
import { DagView } from './DagView'
import { filterTasksByTag, taskNoMatchCause, taskTagOptions } from './taskGraphState'
import { useTaskPreference } from './taskCollectionState'

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

  it('derives completion from the scoped task DAG', () => {
    expect(scopedGraphMetrics([
      { id: 'completed', title: 'Completed scoped task', status: 'done' },
      { id: 'open', title: 'Open scoped task', status: 'open' },
    ] as TaskItem[])).toEqual({ completion_pct: 50 })
  })

  it('uses named native buttons for graph nodes and activates them from the keyboard', async () => {
    const open = vi.fn()
    render(<TaskGraph tasks={tasks} onOpen={open} />)

    const node = screen.getByRole('button', { name: 'Open task: Plan release' })
    node.focus()
    await userEvent.keyboard('{Enter}')

    expect(open).toHaveBeenCalledWith('plan')
  })

  it('keeps gate actions as siblings of the accessible node button', () => {
    const open = vi.fn(), approve = vi.fn(), deny = vi.fn()
    render(<DagView width={200} height={100} nodes={[{ id: 'gate', x: 0, y: 0, w: 160, h: 44, state: 'awaiting', label: 'Review deployment', content: 'Review deployment' }]} edges={[]} onNodeClick={open} onApprove={approve} onDeny={deny} />)

    const node = screen.getByRole('button', { name: 'Review deployment' })
    fireEvent.keyDown(node, { key: ' ' })
    expect(open).toHaveBeenCalledWith('gate')
    expect(node.contains(screen.getByRole('button', { name: 'Approve' }))).toBe(false)
    expect(node.contains(screen.getByRole('button', { name: 'Deny' }))).toBe(false)
  })

  it('treats an empty URL scope as authoritative and writes Clear through', () => {
    localStorage.setItem('tasks-scope', 'Remembered project')
    const setUrl = vi.fn()
    const { result } = renderHook(() => useTaskPreference('', 'tasks-scope', '', setUrl, true))

    expect(result.current[0]).toBe('')
    act(() => result.current[1](''))
    expect(setUrl).toHaveBeenCalledWith('')
    expect(localStorage.getItem('tasks-scope')).toBe('')
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
