import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, act, waitFor } from '@testing-library/react'
import type { AlwaysOnItem, AlwaysOnResponse } from '../../shared/data/api'


const notify = vi.fn()
vi.mock('../../app/shell/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

const alwaysOn = vi.fn()
const alwaysOnDoc = vi.fn()
const saveAlwaysOnDoc = vi.fn()
const projects = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    alwaysOn: (...a: unknown[]) => alwaysOn(...a),
    alwaysOnDoc: (...a: unknown[]) => alwaysOnDoc(...a),
    saveAlwaysOnDoc: (...a: unknown[]) => saveAlwaysOnDoc(...a),
    projects: (...a: unknown[]) => projects(...a),
  },
}))

const { AlwaysOnConventions } = await import('./AlwaysOnConventions')

const SKILL: AlwaysOnItem = {
  id: 'always_skill:house-conventions', kind: 'always_skill', name: 'house-conventions',
  scope: 'global', source: 'user', path: '/home/skills/house-conventions/SKILL.md', chars: 120,
  editable: false, read_only_reason: 'Edit the skill in the Skills area', project_id: '',
  preview: 'Never rewrite history in a ledger.',
}
const OVERVIEW: AlwaysOnItem = {
  id: 'project_instruction:overview.md', kind: 'project_instruction', name: 'overview.md',
  scope: 'project', source: 'project:p-1', path: '/home/projects/p-1/context/overview.md',
  chars: 64, editable: true, read_only_reason: '', project_id: 'p-1',
  preview: 'The deck is stripped. token: [redacted]',
}
const LEDGER: AlwaysOnItem = {
  id: 'project_instruction:decisions.md', kind: 'project_instruction', name: 'decisions.md',
  scope: 'project', source: 'project:p-1', path: '/home/projects/p-1/context/decisions.md',
  chars: 32, editable: false,
  read_only_reason: 'Append-only history — a ledger records what happened.',
  project_id: 'p-1', preview: 'Chose a standing-seam roof.',
}
const VERBATIM = 'The deck is stripped. token: sk-ant-REAL-SECRET-VALUE'

const response = (items: AlwaysOnItem[]): AlwaysOnResponse => ({
  items,
  project_id: 'p-1',
  counts: {
    total: items.length,
    always_skills: items.filter((i) => i.kind === 'always_skill').length,
    project_instructions: items.filter((i) => i.kind === 'project_instruction').length,
  },
  always_skill_mechanism: 'always: true in a skill’s SKILL.md frontmatter',
})

beforeEach(() => {
  notify.mockReset()
  alwaysOn.mockReset().mockResolvedValue(response([SKILL, OVERVIEW, LEDGER]))
  alwaysOnDoc.mockReset().mockResolvedValue({ ...OVERVIEW, body: VERBATIM })
  saveAlwaysOnDoc.mockReset()
  projects.mockReset().mockResolvedValue([{ id: 'p-1', name: 'Roofing Rebuild' }])
})

async function mount() {
  const view = render(<AlwaysOnConventions />)
  await waitFor(() => expect(view.queryByText('house-conventions')).not.toBeNull())
  return view
}

describe('AlwaysOnConventions', () => {
  it('lists both tiers with provenance and counts', async () => {
    const { getByText } = await mount()
    expect(getByText('Always-on skills')).toBeTruthy()
    expect(getByText('Project instructions')).toBeTruthy()
    expect(getByText('user')).toBeTruthy()
    expect(getByText('overview.md')).toBeTruthy()
  })

  it('names the mechanism instead of rendering a blank always-on tier', async () => {
    alwaysOn.mockResolvedValue(response([OVERVIEW]))
    const { findByText } = render(<AlwaysOnConventions />)
    const empty = await findByText(/No skill is always-on yet/)
    expect(empty.textContent).toContain('always: true')
  })

  it('shows a read-only item’s reason and offers no Edit control for it', async () => {
    const { getByText, getAllByRole } = await mount()
    expect(getByText(/Append-only history/)).toBeTruthy()
    expect(getByText(/Edit the skill in the Skills area/)).toBeTruthy()
    expect(getAllByRole('button', { name: 'Edit' })).toHaveLength(1)
  })

  it('opens the editor with the VERBATIM body, not the redacted preview', async () => {
    const { getAllByRole, findByRole } = await mount()
    await act(async () => { fireEvent.click(getAllByRole('button', { name: 'Edit' })[0]) })
    const box = await findByRole('textbox') as HTMLTextAreaElement
    expect(alwaysOnDoc).toHaveBeenCalledWith('project_instruction:overview.md', 'p-1')
    expect(box.value).toBe(VERBATIM)
    expect(box.value).not.toContain('[redacted]')
  })

  it('saves the draft and reports what the store now holds', async () => {
    saveAlwaysOnDoc.mockResolvedValue({ ok: true, item: { ...OVERVIEW, body: 'edited text' } })
    const { getAllByRole, findByRole, getByRole } = await mount()
    await act(async () => { fireEvent.click(getAllByRole('button', { name: 'Edit' })[0]) })
    const box = await findByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'edited text' } })
    await act(async () => { fireEvent.click(getByRole('button', { name: 'Save' })) })
    expect(saveAlwaysOnDoc).toHaveBeenCalledWith('project_instruction:overview.md', 'p-1', 'edited text')
    expect(notify).toHaveBeenCalledWith(expect.stringContaining('Saved overview.md'), 'success')
    expect(box.value).toBe('edited text')
  })

  it('a failed save reports the failure and KEEPS the draft on screen', async () => {
    saveAlwaysOnDoc.mockRejectedValue(new Error('permission denied'))
    const { getAllByRole, findByRole, getByRole } = await mount()
    await act(async () => { fireEvent.click(getAllByRole('button', { name: 'Edit' })[0]) })
    const box = await findByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'my only copy of this edit' } })
    await act(async () => { fireEvent.click(getByRole('button', { name: 'Save' })) })

    const [message, level] = notify.mock.calls.at(-1) as [string, string]
    expect(level).toBe('error')
    expect(message).toContain('permission denied')
    expect(message).toContain('NOT saved')
    expect(box.value).toBe('my only copy of this edit')
  })

  it('re-queries when the project selection changes and closes any open editor', async () => {
    const { getAllByRole, findByRole, getByLabelText, queryByRole } = await mount()
    await act(async () => { fireEvent.click(getAllByRole('button', { name: 'Edit' })[0]) })
    expect(await findByRole('textbox')).toBeTruthy()

    await act(async () => {
      fireEvent.change(getByLabelText('Show project instructions for'), { target: { value: 'p-1' } })
    })
    expect(alwaysOn).toHaveBeenLastCalledWith('p-1')
    expect(queryByRole('textbox')).toBeNull()
  })

  it('surfaces a load failure instead of rendering an empty viewer', async () => {
    alwaysOn.mockRejectedValue(new Error('boom'))
    const { findByText } = render(<AlwaysOnConventions />)
    expect(await findByText(/boom/)).toBeTruthy()
  })
})
