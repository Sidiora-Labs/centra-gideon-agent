import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'


const HERE = dirname(fileURLToPath(import.meta.url))
const PANEL = join(HERE, 'SecretsPanel.tsx')
const API = join(HERE, '../../shared/data/api.ts')

const HINT =
  'No secrets stored yet. Add one here, then reference it from a workflow or automation '
  + 'as {{secret:NAME}} — the value is written once and never read back out.'

const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

type Row = {
  name: string
  scope: 'global' | 'project' | 'host'
  project_id?: string
  consumers?: { kind: 'workflow' | 'trigger'; id: string; label: string }[]
}

const row = (r: Row) => ({
  name: r.name,
  scope: r.scope,
  project_id: r.project_id ?? '',
  present: true as const,
  inherited_from_host: r.scope === 'host',
  consumers: r.consumers ?? [],
})

const payload = (rows: Row[]) => {
  const secrets = rows.map(row)
  return {
    secrets,
    counts: {
      total: secrets.length,
      global: secrets.filter((s) => s.scope === 'global').length,
      project: secrets.filter((s) => s.scope === 'project').length,
      host: secrets.filter((s) => s.scope === 'host').length,
    },
    empty_hint: secrets.length === 0 ? HINT : '',
  }
}

const putSecret = vi.fn()
const deleteSecret = vi.fn()

async function mount(secrets: () => Promise<unknown>) {
  vi.resetModules()
  sessionStorage.clear()
  putSecret.mockReset().mockResolvedValue({ secret: {}, secrets: [] })
  deleteSecret.mockReset().mockResolvedValue({ deleted: '', project_id: '', secrets: [] })
  vi.doMock('../../shared/ui/dialog', () => ({ confirm: vi.fn(async () => true) }))
  vi.doMock('../../shared/data/api', () => ({ api: { secrets, putSecret, deleteSecret } }))
  const { SecretsPanel } = await import('./SecretsPanel')
  await act(async () => {
    render(<SecretsPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); vi.resetModules(); vi.restoreAllMocks() })

describe('a stored secret renders as presence, never as a value', () => {
  it('shows the name and a "set" marker, and no value anywhere in the DOM', async () => {
    await mount(async () => payload([
      { name: 'GITHUB_TOKEN', scope: 'global' },
      { name: 'DB_PASSWORD', scope: 'project', project_id: 'proj-a' },
    ]))
    expect(screen.getByText('GITHUB_TOKEN')).toBeTruthy()
    expect(screen.getByText('DB_PASSWORD')).toBeTruthy()
    expect(screen.getAllByText(/^set$/i).length).toBe(2)
    expect(screen.getByText('proj-a')).toBeTruthy()
  })

  it('says out loud when nothing references a secret', async () => {
    await mount(async () => payload([{ name: 'UNUSED_TOKEN', scope: 'global' }]))
    expect(screen.getByText(/not referenced by any workflow or automation/i)).toBeTruthy()
  })

  it('renders each derived consumer link', async () => {
    await mount(async () => payload([{
      name: 'GITHUB_TOKEN',
      scope: 'global',
      consumers: [
        { kind: 'workflow', id: 'nightly-sync', label: 'Nightly sync' },
        { kind: 'trigger', id: 't-1', label: 'Morning digest' },
      ],
    }]))
    expect(screen.getByText(/used by/i)).toBeTruthy()
    expect(screen.getByText('Nightly sync')).toBeTruthy()
    expect(screen.getByText('Morning digest')).toBeTruthy()
    expect(screen.queryByText(/not referenced by any/i)).toBeNull()
  })
})

describe('an inherit-from-host row is not a vault row', () => {
  it('is marked as coming from the host environment', async () => {
    await mount(async () => payload([{ name: 'SSH_AUTH_SOCK', scope: 'host' }]))
    expect(screen.getByText('SSH_AUTH_SOCK')).toBeTruthy()
    expect(screen.getByText(/from host environment/i)).toBeTruthy()
    expect(screen.queryByText(/^set$/i)).toBeNull()
  })

  it('its Remove control is reachable but refuses, WITH the reason', async () => {
    await mount(async () => payload([{ name: 'SSH_AUTH_SOCK', scope: 'host' }]))
    const remove = screen.getByRole('button', { name: /remove/i })
    expect(remove.getAttribute('aria-disabled')).toBe('true')
    expect(remove.getAttribute('title') ?? '').toMatch(/gateway's environment/i)
    await act(async () => { remove.click() })
    expect(deleteSecret).not.toHaveBeenCalled()
  })

  it('a stored secret CAN be removed — so the refusal above is about the scope, not the button', async () => {
    await mount(async () => payload([{ name: 'GITHUB_TOKEN', scope: 'global' }]))
    const remove = screen.getByRole('button', { name: /remove/i })
    expect(remove.getAttribute('aria-disabled')).toBeNull()
    await act(async () => { remove.click() })
    expect(deleteSecret).toHaveBeenCalledWith('GITHUB_TOKEN', '')
  })
})

describe('the honest read', () => {
  it('an empty vault carries the SERVER\'s next-action sentence', async () => {
    await mount(async () => payload([]))
    expect(screen.getByText(/no secrets yet/i)).toBeTruthy()
    expect(screen.getByText(/no secrets stored yet\. add one here/i)).toBeTruthy()
    expect(screen.getByText(/never read back out/i)).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('a failed read renders an alert, never an empty vault', async () => {
    await mount(async () => { throw new Error('boom') })
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText(/couldn't load your secrets vault/i)).toBeTruthy()
    expect(screen.queryByText(/no secrets yet/i)).toBeNull()
  })
})

describe('the write path is one-way, in the source as well as at runtime', () => {
  it('the value input is masked and the panel declares no value field', () => {
    const src = readFileSync(PANEL, 'utf8')
    expect(src, 'the value field must be masked').toMatch(/type="password"/)
    expect(codeOnly(src)).not.toMatch(/\breveal\b/i)
    expect(codeOnly(src)).not.toMatch(/navigator\.clipboard/)
    expect(codeOnly(src)).toMatch(/type="password"/)
  })

  it('the wire type has no value field', () => {
    const src = readFileSync(API, 'utf8')
    const iface = /export interface SecretPresenceWire \{([^}]*)\}/.exec(src)?.[1] ?? ''
    expect(iface.length, 'SecretPresenceWire not found in api.ts').toBeGreaterThan(20)
    expect(iface).toMatch(/name: string/)
    expect(iface).not.toMatch(/\bvalue\b/)
    expect(iface).not.toMatch(/\bplaintext\b/)
  })

  it('there is no client method that reads a secret back', () => {
    const src = readFileSync(API, 'utf8')
    expect(src).not.toMatch(/getSecret\s*:/)
    expect(src).not.toMatch(/revealSecret\s*:/)
    expect(src).toMatch(/\bsecrets:\s*\(projectId/)
    expect(src).toMatch(/\bputSecret:\s*\(/)
    expect(src).toMatch(/\bdeleteSecret:\s*\(/)
  })
})
