import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { TriageDigestCard } from './TriageDigestCard'
import type { TriageDigestView, TriagePending } from '../../shared/data/api'

const proactiveDigest = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: { proactiveDigest: () => proactiveDigest() },
}))

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

const pending = (ordinal: string, title: string): TriagePending => ({
  ordinal,
  action_type: 'reply_draft',
  tier: 'medium',
  pattern_key: 'reply_draft:inbox',
  clamped: false,
  reason: 'needs_you',
  rule: '',
  answered: false,
  answer: '',
  permalink: '#/workflows/runs/run-abc',
  title,
  source: 'inbox',
  item_permalink: `#/inbox?open=${ordinal}`,
  materiality: 'action',
})

const view = (rows: TriagePending[]): TriageDigestView => ({
  state: 'ready',
  enabled: true,
  installed: true,
  error: '',
  run_id: 'run-abc',
  permalink: '#/workflows/runs/run-abc',
  title: 'Morning triage',
  collected: rows.length,
  dropped: 0,
  auto_stage_ran: true,
  auto_done: [],
  pending: rows,
  machine_did: [],
  ledger_complete: true,
  ledger_rows: 0,
})

beforeEach(() => proactiveDigest.mockReset())

describe('digest pending-row accessible names', () => {
  it('keeps visible actions short while naming the proposal they affect', async () => {
    proactiveDigest.mockResolvedValue(view([
      pending('1', 'Review request on #412'),
      pending('2', 'Review request on #412'),
    ]))

    render(<TriageDigestCard />)

    const firstYes = await screen.findByRole('button', { name: 'Yes: #1 Review request on #412' })
    expect(firstYes.textContent).toBe('Yes')
    expect(screen.getByRole('button', { name: 'No: #1 Review request on #412' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Always: #1 Review request on #412' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Never: #1 Review request on #412' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Yes: #2 Review request on #412' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open item: #1 Review request on #412' })).toBeTruthy()
  })

  it('uses the digest ordinal when a proposal has no title', async () => {
    proactiveDigest.mockResolvedValue(view([pending('7', '')]))

    render(<TriageDigestCard />)

    expect(await screen.findByRole('button', { name: 'Yes: item #7' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open item: item #7' })).toBeTruthy()
  })
})
