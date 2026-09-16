import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { TaskDetail } from './TaskDetail'
import { blockKindMeta } from './taskMeta'
import type { TaskItem } from '../../shared/data/api'

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: { updateTask: vi.fn(), deleteTask: vi.fn(), taskComments: () => Promise.resolve([]), taskNotes: () => Promise.resolve([]) },
}))


const base: TaskItem = { id: 't1', title: 'Rebuild the stall layout', status: 'blocked' }

const AUTO: TaskItem = {
  ...base,
  blocked_reason_kind: 'auto',
  block_reason: {
    is_blocked: true,
    blocking_task_ids: ['t9'],
    blocking_task_titles: ['Deliver the gallery'],
    message: 'Waiting on: Deliver the gallery',
  },
}

const MANUAL: TaskItem = {
  ...base,
  blocked_reason_kind: 'manual',
  block_reason: { is_blocked: false, blocking_task_ids: [], blocking_task_titles: [], message: '' },
}

const mount = (task: TaskItem) => render(
  <TaskDetail task={task} onSaved={() => {}} onDeleted={() => {}} editing={false} onEditingChange={() => {}} />,
)

describe('blockKindMeta', () => {
  it('distinguishes a self-clearing block from one only a person can clear', () => {
    expect(blockKindMeta('auto')?.label).toBe('Waiting on a prerequisite')
    expect(blockKindMeta('manual')?.label).toBe('Blocked by you')
  })

  it('says of an auto block that it unblocks itself', () => {
    expect(blockKindMeta('auto')?.hint).toMatch(/unblocks itself/i)
  })

  it('says of a manual block that it waits for the user', () => {
    expect(blockKindMeta('manual')?.hint).toMatch(/until you unblock it/i)
  })

  it('returns null for an unstamped or unknown kind', () => {
    expect(blockKindMeta('')).toBeNull()
    expect(blockKindMeta(undefined)).toBeNull()
    expect(blockKindMeta('something_new')).toBeNull()
  })
})

describe('a manually-blocked task explains itself', () => {
  it('renders the blocked panel even though is_blocked is false', () => {
    const text = mount(MANUAL).container.textContent ?? ''
    expect(text).toContain('Blocked by you')
  })

  it('states that it stays blocked until the user acts', () => {
    expect(mount(MANUAL).container.textContent).toMatch(/until you unblock it/i)
  })

  it('never renders a bare "Waiting on" with nothing after it', () => {
    const text = mount(MANUAL).container.textContent ?? ''
    expect(text).not.toMatch(/Waiting on\s*$/)
    expect(text).not.toContain('Waiting on ,')
  })
})

describe('an auto-blocked task keeps naming its prerequisite', () => {
  it('still shows which task it is waiting on', () => {
    expect(mount(AUTO).container.textContent).toContain('Deliver the gallery')
  })

  it('labels it as waiting on a prerequisite rather than the generic word', () => {
    expect(mount(AUTO).container.textContent).toContain('Waiting on a prerequisite')
  })
})

describe('the panel stays honest in the edge cases', () => {
  it('renders no blocked panel for a task that is not blocked', () => {
    const text = mount({ ...base, status: 'open', blocked_reason_kind: 'manual' }).container.textContent ?? ''
    expect(text).not.toContain('Blocked by you')
    expect(text).not.toContain('Waiting on a prerequisite')
  })

  it('falls back to block_reason when no kind is stamped', () => {
    const text = mount({ ...AUTO, blocked_reason_kind: undefined }).container.textContent ?? ''
    expect(text).toContain('Blocked')
    expect(text).toContain('Deliver the gallery')
  })

  it('renders nothing when neither the kind nor block_reason says blocked', () => {
    const text = mount({ ...base, status: 'blocked' }).container.textContent ?? ''
    expect(text).not.toContain('Blocked by you')
    expect(text).not.toMatch(/Waiting on/)
  })
})
