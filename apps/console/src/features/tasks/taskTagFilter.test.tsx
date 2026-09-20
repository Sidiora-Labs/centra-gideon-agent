import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const tasks = [
  { id: 'alpha', title: 'Alpha task', status: 'open', priority: 'medium', labels: ['release'] },
  { id: 'beta', title: 'Beta task', status: 'open', priority: 'medium', labels: ['planning'] },
]

vi.mock('../../shared/data/api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../shared/data/api')>()
  return { ...original, api: { ...original.api,
    tasks: vi.fn(async () => ({ tasks, owner: '' })), projects: vi.fn(async () => []),
    taskLists: vi.fn(async () => []), uLoops: vi.fn(async () => []), taskGraph: vi.fn(async () => ({ analysis: null })),
  } }
})

import { TasksListPage } from './TasksListPage'

afterEach(cleanup)

describe('task tag chips', () => {
  it('are reachable buttons that filter the visible task collection', async () => {
    const noop = () => {}
    render(<TasksListPage onCreate={noop} view="list" filter="all" openId={null} setView={noop} setFilter={noop}
      setOpenId={noop} editing={false} setEditing={noop} q="" sort="" scope="" list="" setQ={noop} setSort={noop} setScope={noop} setList={noop} />)

    await screen.findByText('Alpha task')
    fireEvent.click(screen.getByRole('button', { name: 'Filter by tag “release”' }))

    expect(screen.getByText('Alpha task')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText('Beta task')).toBeNull())
  })
})
