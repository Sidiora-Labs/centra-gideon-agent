import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { PackRow } from './PacksPanel'
import type { InstalledPackRec } from '../../shared/data/api'

vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))


const base: InstalledPackRec = {
  name: 'cfo-pack',
  version: '1.2.0',
  components: ['skill:cfo-report', 'trigger:month-end'],
  connectors: [
    { name: 'quickbooks', mode: 'configure', server_name: 'qb-mcp', marker: '', credentials_saved: ['qb_token'], error: '' },
    { name: 'slack', mode: 'substitute', server_name: 'slack-alt', marker: '', credentials_saved: [], error: '' },
    { name: 'netsuite', mode: 'skip', server_name: '', marker: 'connector_missing:netsuite', credentials_saved: [], error: 'no credential provided' },
  ],
  connector_markers: ['connector_missing:netsuite'],
  setup_skill: 'cfo-pack-setup',
  setup_pending: false,
  installed_at: '2026-08-01T09:30:00Z',
}

const text = (p: InstalledPackRec) => render(<PackRow pack={p} />).container.textContent ?? ''

describe('components — what the pack installed', () => {
  it('lists every installed component', () => {
    const t = text(base)
    expect(t).toContain('Installed')
    expect(t).toContain('skill:cfo-report')
    expect(t).toContain('trigger:month-end')
  })

  it('omits the Installed line for a pack that installed nothing', () => {
    const { container } = render(<PackRow pack={{ ...base, components: [] }} />)
    expect([...container.querySelectorAll('span')].map((s) => s.textContent))
      .not.toContain('Installed')
    expect(container.textContent).toContain('Installed 8/1/2026')
  })

  it('survives a record with no components key at all', () => {
    const t = text({ ...base, components: undefined as unknown as string[] })
    expect(t).toContain('cfo-pack')
  })
})

describe('connectors — how each one resolved, not just which failed', () => {
  it('names every connector and its mode', () => {
    const t = text(base)
    expect(t).toContain('quickbooks')
    expect(t).toContain('configure')
    expect(t).toContain('slack')
    expect(t).toContain('substitute')
    expect(t).toContain('netsuite')
    expect(t).toContain('skip')
  })

  it('shows which server a configured connector wrote', () => {
    expect(text(base)).toContain('qb-mcp')
  })

  it('shows the credential KEYS saved, which is the audit fact', () => {
    expect(text(base)).toContain('saved qb_token')
  })

  it('distinguishes a degraded skip from a pack that never asked', () => {
    expect(text(base)).toContain('no credential provided')
  })

  it('tones a skipped connector as a warning and leaves the others neutral', () => {
    const { container } = render(<PackRow pack={base} />)
    const warned = [...container.querySelectorAll('.text-warn')].map((e) => e.textContent)
    expect(warned).toContain('netsuite')
    expect(warned).not.toContain('quickbooks')
  })

  it('still shows configured connectors for a pack with NO skips', () => {
    const t = text({ ...base, connectors: [base.connectors[0]], connector_markers: [] })
    expect(t).toContain('quickbooks')
    expect(t).toContain('qb-mcp')
  })
})

describe('setup_skill and installed_at', () => {
  it('names the setup skill once setup is no longer pending', () => {
    expect(text(base)).toContain('Setup skill: cfo-pack-setup')
  })

  it('does NOT name it while the chip is showing', () => {
    const t = text({ ...base, setup_pending: true })
    expect(t).toContain('Finish setup')
    expect(t).not.toContain('Setup skill:')
  })

  it('shows the install date', () => {
    expect(text(base)).toContain('Installed 8/1/2026')
  })

  it('renders nothing rather than "Invalid Date" for a malformed timestamp', () => {
    const t = text({ ...base, installed_at: 'not-a-date' })
    expect(t).not.toContain('Invalid Date')
  })

  it('omits the date line when the field is empty', () => {
    const t = text({ ...base, installed_at: '', components: [], connectors: [], setup_skill: '' })
    expect(t).not.toMatch(/Installed \d/)
  })
})

describe('the detail block gate', () => {
  it('renders for a pack whose ONLY extra fact is its install date', () => {
    const t = text({ ...base, components: [], connectors: [], setup_skill: '', connector_markers: [] })
    expect(t).toContain('Installed 8/1/2026')
  })

  it('renders no detail block for a bare record', () => {
    const { container } = render(<PackRow pack={{
      name: 'bare', version: '0.1', components: [], connectors: [], connector_markers: [],
      setup_skill: '', setup_pending: false, installed_at: '',
    }} />)
    expect(container.textContent).toContain('bare')
    expect(container.querySelector('.border-t')).toBeNull()
  })
})
