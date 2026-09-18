import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { AuditPage, SelEvent } from '../../shared/data/api'
import { AuditPanel } from './AuditPanel'

const auditEvents = vi.fn()
const auditVerify = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    auditEvents: (...a: unknown[]) => auditEvents(...a),
    auditVerify: (...a: unknown[]) => auditVerify(...a),
    selRotate: vi.fn(),
  },
}))
vi.mock('../../shared/data/data', () => ({ invalidateKeys: vi.fn() }))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../shared/ui/dialog', () => ({ confirm: vi.fn() }))

const page = (events: SelEvent[]) =>
  ({ events, count: events.length, next_cursor: '', scanned: events.length, truncated: false, outcome_families: [] } as unknown as AuditPage)

const granted: SelEvent = {
  event_id: 'g1', timestamp: '2026-09-01T10:00:00+00:00', event_type: 'api_access',
  caller_identity: '127.0.0.1', source: 'token_auth', operation: 'internal_auth',
  outcome: 'granted', resources: '/api/spawn', error: '',
  metadata: { reason: 'cookie auth (no secret header)' },
}
const denied: SelEvent = {
  event_id: 'd1', timestamp: '2026-09-01T10:00:01+00:00', event_type: 'api_access',
  caller_identity: '127.0.0.1', source: 'token_auth', operation: 'internal_auth',
  outcome: 'denied', resources: '/api/spawn', error: 'wrong secret',
}

describe('a granted event shows its rationale as a reason, not an error', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auditVerify.mockResolvedValue({ ok: true, checked: 1 })
  })

  it('renders reason for a granted event and no error line', async () => {
    auditEvents.mockResolvedValue(page([granted]))
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /internal_auth/ }))
    expect(screen.getByText('reason:')).toBeTruthy()
    expect(screen.getByText('cookie auth (no secret header)')).toBeTruthy()
    expect(screen.queryByText('error:')).toBeNull()
  })

  it('still renders error for a denied event', async () => {
    auditEvents.mockResolvedValue(page([denied]))
    render(<AuditPanel />)
    fireEvent.click(await screen.findByRole('button', { name: /internal_auth/ }))
    expect(screen.getByText('error:')).toBeTruthy()
    expect(screen.getByText('wrong secret')).toBeTruthy()
    expect(screen.queryByText('reason:')).toBeNull()
  })
})
