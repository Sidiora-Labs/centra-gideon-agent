import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { AuditPage } from '../../shared/data/api'
import { AuditPanel } from './AuditPanel'


const auditEvents = vi.fn()
const auditVerify = vi.fn()
const selRotate = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: (...a: unknown[]) => selRotate(...a),
  },
}))
vi.mock('../../shared/data/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

const FAMILIES: AuditPage['outcome_families'] = [
  { key: 'denied', label: 'Denied', values: ['denied', 'rejected', 'blocked', 'refused'] },
  { key: 'failed', label: 'Failed', values: ['failure', 'failed', 'error', 'not_found'] },
]

const page = (over: Partial<AuditPage> = {}): AuditPage => ({
  events: [{ event_id: 'e1', timestamp: '2026-08-19T10:00:00Z', event_type: 'tool', outcome: 'error', operation: 'DELETE /api/terminal/sessions/abc' }],
  count: 1, next_cursor: '', scanned: 1, truncated: false, outcome_families: FAMILIES, ...over,
})

describe('the outcome pills come from the log, not from this panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auditEvents.mockResolvedValue(page())
  })

  it('renders one pill per family the server sent, plus All', async () => {
    render(<AuditPanel />)
    const group = await screen.findByRole('group', { name: 'Filter by outcome' })
    await waitFor(() => expect(group.querySelectorAll('button').length).toBe(3))
    expect([...group.querySelectorAll('button')].map((b) => b.textContent)).toEqual(['All', 'Denied', 'Failed'])
  })

  it('a family the server adds appears without touching this file', async () => {
    auditEvents.mockResolvedValue(page({
      outcome_families: [...FAMILIES, { key: 'warned', label: 'Warned', values: ['needs_confirm'] }],
    }))
    render(<AuditPanel />)
    const group = await screen.findByRole('group', { name: 'Filter by outcome' })
    await waitFor(() => expect(group.querySelectorAll('button').length).toBe(4))
    expect(screen.getByRole('button', { name: 'Warned' })).toBeTruthy()
  })

  it('clicking a pill queries the WHOLE family, server-side', async () => {
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Failed' }))
    await waitFor(() => {
      const last = auditEvents.mock.calls.at(-1)?.[0] as { filters: { outcome?: string } }
      expect(last.filters.outcome).toBe('failure,failed,error,not_found')
    }, { timeout: 2000 })
  })

  it('the pressed pill is the one whose family is applied', async () => {
    render(<AuditPanel />)
    const failed = await screen.findByRole('button', { name: 'Failed' })
    expect(failed.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(failed)
    await waitFor(() => expect(failed.getAttribute('aria-pressed')).toBe('true'))
    const group = screen.getByRole('group', { name: 'Filter by outcome' })
    expect([...group.querySelectorAll('button')].filter((b) => b.getAttribute('aria-pressed') === 'true').length).toBe(1)
  })

  it('holds no outcome vocabulary of its own', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), "src/features/settings/AuditPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'no local preset list').not.toMatch(/OUTCOME_PRESETS/)
    expect(src, 'the pills are the served families').toMatch(/const presets = \[ALL_PRESET, \.\.\.families\]/)
    expect(src, 'and the click sends the joined family').toMatch(/f\.values\.join\(','\)/)
  })
})
