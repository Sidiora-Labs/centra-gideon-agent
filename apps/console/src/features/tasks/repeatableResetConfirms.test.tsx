import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const resetMock = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({ ok: true, reset_task_ids: [] }))

const PROJECT = { id: 'p-rep', name: 'Repeatable', status: 'active' }
const LIST = { id: 'tl-1', name: 'Morning checklist', project_id: 'p-rep' }
const TASK = {
  id: 't-1', title: 'Water the plants', status: 'done', priority: 'medium',
  task_list_id: 'tl-1', execution_notes: [{ at: 1, text: 'did it' }],
}

vi.mock('../../shared/data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../shared/data/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      tasks: vi.fn(async () => ({ tasks: [TASK], owner: 'me' })),
      projects: vi.fn(async () => [PROJECT]),
      taskLists: vi.fn(async () => [LIST]),
      readyTasks: vi.fn(async () => []),
      searchTasks: vi.fn(async () => ({ results: [] })),
      uLoops: vi.fn(async () => []),
      tasksBulk: vi.fn(async () => ({ ok: true })),
      updateTask: vi.fn(async () => ({})),
      resetTaskList: (...a: unknown[]) => resetMock(...a),
    },
  }
})
vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))

import { TasksListPage } from './TasksListPage'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'

const noop = () => {}
function mount() {
  return render(
    <>
      <TasksListPage
        onCreate={noop} view="list" filter="all" openId="" setView={noop} setFilter={noop}
        setOpenId={noop} editing={false} setEditing={noop}
        q="" sort="" scope="Repeatable" list="" setQ={noop} setSort={noop} setScope={noop} setList={noop}
      />
      <DialogHost />
    </>,
  )
}

const findReset = () => waitFor(() => screen.getByRole('button', { name: `Reset list ${LIST.name}` }))

beforeEach(() => { resetMock.mockClear(); sessionStorage.clear() })
afterEach(() => cleanup())

describe('resetting a Repeatable list asks first', () => {
  it('a declined confirm destroys NOTHING', async () => {
    mount()
    fireEvent.click(await findReset())
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(resetMock, 'declining must not reach the API').not.toHaveBeenCalled()
  })

  it('🔑 the dialog names the list AND what is lost, not the precondition', async () => {
    mount()
    fireEvent.click(await findReset())
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    expect(within(dlg).getByText(new RegExp(`Reset .${LIST.name}`))).toBeInTheDocument()
    expect(within(dlg).getByText(/execution notes are cleared/i)).toBeInTheDocument()
    expect(within(dlg).getByText(/cannot be recovered/i)).toBeInTheDocument()
    expect(within(dlg).queryByText(/must be done/i)).toBeNull()
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  })

  it('a confirmed reset calls the API exactly once, with the list id', async () => {
    mount()
    fireEvent.click(await findReset())
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /reset list/i }))
    await waitFor(() => expect(resetMock).toHaveBeenCalledTimes(1))
    expect(resetMock).toHaveBeenCalledWith(LIST.id)
  })
})

describe('the gate is real, and the copy makes no promise it cannot keep', () => {
  const code = readFileSync(join(process.cwd(), "src/features/tasks/TasksListPage.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
  const body = code.match(/async function resetList\(list: TaskListItem\)[\s\S]*?\n  \}/)?.[0] ?? ''

  it('confirm precedes the destroy', () => {
    expect(body, 'found resetList').not.toBe('')
    expect(body).toMatch(/if \(!\(await confirm\(\{/)
    expect(body.indexOf('await confirm('), 'the confirm comes first')
      .toBeLessThan(body.indexOf('api.resetTaskList'))
  })

  it('it is danger-toned, so the dialog is an alertdialog', () => {
    expect(body).toMatch(/danger: true/)
  })

  it('🪤 the body states the consequence CATEGORICALLY — no task count', () => {
    const dialogArgs = body.match(/await confirm\(\{[\s\S]*?\}\)/)?.[0] ?? ''
    expect(dialogArgs, 'found the confirm options').not.toBe('')
    expect(dialogArgs, 'no count is interpolated').not.toMatch(/\.length/)
    expect(dialogArgs, 'and it speaks for every task').toMatch(/Every task/)
  })

  it('VACUITY: the server really does clear the notes this dialog warns about', () => {
    const py = readFileSync(
      join(process.cwd(), "../../runtime/gideon/engine/tasks/hierarchy_handlers.py"), 'utf8',
    )
    const handler = py.match(/async def api_task_lists_reset[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the reset handler').not.toBe('')
    expect(handler, 'the route delegates to its reset owner').toMatch(/RepeatableListReset\(request\.match_info\["list_id"\]\)\.respond\(\)/)
    const owner = py.match(/class RepeatableListReset:[\s\S]*?(?=\ndef |\nclass |$)/)?.[0] ?? ''
    expect(owner, 'the reset owner really reaches every task').toMatch(/for task in tasks:/)
    expect(owner, 'it still clears execution notes for every task').toMatch(/execution_notes=\[\]/)
  })
})
