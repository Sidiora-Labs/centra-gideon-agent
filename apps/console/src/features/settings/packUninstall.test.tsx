import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { PackUninstallRec } from '../../shared/data/api'
import { PackUninstallBlockers, UninstallPlanDetails, uninstallSummaryText } from './PacksPanel'

const plan: PackUninstallRec = {
  pack: 'personal-cfo',
  version: '1.0.0',
  removed: [
    'skill:cfo-statement-fetch',
    'skill:personal-cfo-setup',
    'agent:cfo',
    'prompt:cfo-spending-digest',
    'template:cfo-monthly-review',
    'trigger:cfo-spending-digest',
  ],
  kept: [{ ref: 'skill:cfo-budget-review', reason: 'you edited it after it was installed, so it stays' }],
  missing: [],
  in_use: [],
  servers: ['finance-statements'],
  applied: false,
  confirmation_token: 'opaque-current-plan',
}

describe('pack uninstall plan', () => {
  it('names removals, kept edits and configured MCP servers in the confirmation content', () => {
    expect(uninstallSummaryText(plan)).toBe(
      'Removes 2 skills, 1 agent definition, 1 prompt, 1 workflow, 1 staged automation.',
    )
    render(<UninstallPlanDetails plan={plan} />)
    expect(screen.getByText(/skill:cfo-budget-review/).closest('li')?.textContent).toContain('you edited it')
    expect(screen.getByText(/MCP server/).textContent).toContain('finance-statements')
  })

  it('names the deployed dependency and the surface where it must be removed', () => {
    render(<PackUninstallBlockers pack="personal-cfo" inUse={[
      { kind: 'agent', id: 'cfo', name: 'cfo' },
      { kind: 'automation', id: 'pack-personal-cfo-spending-digest', name: 'Spending digest' },
    ]} />)
    expect(screen.getByRole('alert').textContent).toContain('personal-cfo is still in use')
    expect(screen.getByRole('link', { name: 'Agents' }).getAttribute('href')).toBe('#/agents')
    expect(screen.getByRole('link', { name: 'Automations' }).getAttribute('href')).toBe('#/triggers')
  })
})
