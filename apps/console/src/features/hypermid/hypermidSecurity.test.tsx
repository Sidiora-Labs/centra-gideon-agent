import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { HypermidSecurityStatusWire } from '../../shared/data/api'
import { SecurityView } from './Security'

const ready: HypermidSecurityStatusWire = {
  scope: { owner_id: 'owner', project_id: 'project' },
  availability: 'ready',
  grants: [{
    grantId: 'grant-1', principalId: 'principal-1', operation: 'model.fetch', scheme: 'https', hostname: 'models.example.test',
    ports: [443], addressClasses: ['public'], proxyPolicy: 'required', redirectLimit: 2, byteLimit: 4_096, expiresAtMs: Date.parse('2026-10-03T12:00:00Z'),
  }],
  decisions: [{ code: 'ALLOW', rule: 'active grant', allowed: true, checkedAtMs: Date.parse('2026-10-02T12:00:00Z') }],
}

describe('Hypermid security status', () => {
  it('renders authoritative grant metadata and distinguishes an unavailable authority from an empty grant set', () => {
    const available = renderToStaticMarkup(<SecurityView status={ready} onRefresh={vi.fn()} onRevoke={vi.fn()} />)
    expect(available).toContain('https://models.example.test')
    expect(available).toContain('ports 443')
    expect(available).toContain('active grant')

    const unavailable = renderToStaticMarkup(<SecurityView status={{
      scope: ready.scope, grants: [], decisions: [], availability: 'unavailable',
      error: { code: 'SECURITY_STATUS_UNAVAILABLE', message: 'network security authority is unavailable', httpStatus: 503 },
    }} onRefresh={vi.fn()} onRevoke={vi.fn()} />)
    expect(unavailable).toContain('Network security status unavailable')
    expect(unavailable).toContain('network security authority is unavailable')
    expect(unavailable).not.toContain('No network access is granted')
  })
})
