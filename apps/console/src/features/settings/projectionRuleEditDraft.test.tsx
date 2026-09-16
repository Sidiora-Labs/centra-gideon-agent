import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ProjectionRule } from '../../shared/data/api'


const RULE: ProjectionRule = { name: 'myapp', match_regex: '^\\[MYAPP\\]', strategy: 'log' }

const setProjectionRules = vi.fn((_rules: ProjectionRule[]) => Promise.resolve({}))

async function mount() {
  vi.resetModules()
  setProjectionRules.mockClear()
  vi.doMock('../../shared/data/api', () => ({
    api: {
      projectionRules: () => Promise.resolve([{ ...RULE }]),
      setProjectionRules: (rules: ProjectionRule[]) => setProjectionRules(rules),
      toolsSavings: () => Promise.resolve(null),
    },
  }))
  const { ProjectionRulesPanel } = await import('./ProjectionRulesPanel')
  await act(async () => {
    render(<ProjectionRulesPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return screen.findByLabelText('Match regex for myapp')
}

describe('editing an existing projection rule (draft-and-commit, #674)', () => {
  it('typing five characters saves nothing and loses nothing', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('QWERT')
    expect(setProjectionRules).not.toHaveBeenCalled()

    expect((field as HTMLInputElement).value).toBe('^\\[MYAPP\\]QWERT')
  })

  it('leaving the row commits exactly one save carrying the full value', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('QWERT')
    await userEvent.tab()

    await userEvent.tab()
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    const sent = setProjectionRules.mock.calls[0][0]
    expect(sent[0].match_regex).toBe('^\\[MYAPP\\]QWERT')
  })

  it('Enter commits from within the field', async () => {
    const field = await mount()
    await userEvent.click(field)
    await userEvent.keyboard('X{Enter}')
    expect(setProjectionRules).toHaveBeenCalledTimes(1)
    expect(setProjectionRules.mock.calls[0][0][0].match_regex).toBe('^\\[MYAPP\\]X')
  })
})
