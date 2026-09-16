import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, render, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PANEL = join(process.cwd(), "src/features/settings/SecurityPanel.tsx")

const SHA = '2b7db3c6d0be84890aff1ad3bf2bcbcbf3bdf5cb6b991079734db1ee10c6e872'
const BUILTIN = Array.from({ length: 112 }, (_, i) => `deny-pattern-${i}`)

const VERIFIED = { version: 1, sha256: SHA, count: 112, verified: true, detail: '' }
const DIVERGED = {
  version: 1, sha256: SHA, count: 112, verified: false,
  detail: 'packaged file no longer matches the verified baseline',
}

type Over = { baseline?: object; user?: string[]; user_additions?: number }

const payload = (over: Over = {}) => ({
  builtin: BUILTIN, user: [], baseline: VERIFIED, user_additions: 0, ...over,
})

async function mount(opts: { denied?: Over | 'reject'; stats?: 'reject' } = {}) {
  vi.resetModules()
  sessionStorage.clear()
  vi.doMock('../../shared/data/api', () => ({
    api: {
      securityStats: () => opts.stats === 'reject'
        ? Promise.reject(new Error('probe-induced 500 on /api/security/stats'))
        : Promise.resolve({
          denied_commands: 112, suspicious_patterns: 20, tool_schemas: 30, redaction_paths: 5,
        }),
      deniedCommands: () => opts.denied === 'reject'
        ? Promise.reject(new Error('probe-induced 500 on /api/security/denied-commands'))
        : Promise.resolve(payload(opts.denied)),
      securityEgress: () => Promise.resolve({ allow_hosts: [], deny_hosts: [], allow_private: false }),
      setUserDeniedCommands: () => Promise.resolve({}),
      setSecurityEgress: () => Promise.resolve({}),
      desktopState: () => Promise.resolve({
        connected: false, shell: null, capabilities: {}, registered_at: '', last_seen: '',
      }),
      credentialStore: () => Promise.resolve({
        migration: 'credentials_to_keychain', backend: 'dotenv', requested: 'dotenv',
        blocked: true, pending_keys: [], pending: 0, keychain_keys: 0,
        rollback_available: false, snapshot_name: '.env.pre-keychain', verified: true,
        verification: { checked: 0, missing: [], still_in_dotenv: [] },
      }),
    },
  }))
  const { SecurityPanel } = await import('./SecurityPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<SecurityPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the baseline indicator renders the verified state', () => {
  it('names the version, the enforced count and the digest in the a11y tree', async () => {
    const r = await mount()
    const chip = r.getByRole('status', {
      name: /Baseline v1 matches what shipped: 112 patterns verified against the release sha256/,
    })
    expect(chip.textContent).toContain('Baseline v1 matches what shipped')
    expect(chip.textContent).toContain('112 patterns')
    expect(chip.textContent, 'the digest is shown truncated').toContain(SHA.slice(0, 12))
    expect(r.queryByRole('alert'), 'a verified baseline is not an alert').toBeNull()
  })

  it('a tamper fixture flips the indicator — role AND copy', async () => {
    const r = await mount({ denied: { baseline: DIVERGED } })

    expect(r.queryByRole('status'), 'the quiet form must be gone').toBeNull()
    const chip = r.getByRole('alert', {
      name: /Baseline v1 does not match what shipped: packaged file no longer matches the verified baseline\. The 112 verified patterns are still enforced\./,
    })
    expect(chip.textContent).toContain('does NOT match what shipped')
    expect(chip.textContent).toContain('112 verified patterns are still enforced')
    expect(r.getAllByText('deny-pattern-0').length, 'the baseline still renders').toBe(1)
  })
})

describe("'N user additions' counts what actually widened the set", () => {
  it('shows the addition count, not the length of the config list', async () => {
    const r = await mount({
      denied: { user: ['deny-pattern-0', 'mine-a', 'deny-pattern-7'], user_additions: 1 },
    })
    const text = r.container.textContent ?? ''

    expect(text).toContain('1 user addition on top of the baseline')
    expect(text, 'the naive count would say 3').not.toContain('3 user additions')
    expect(text).toContain('2 of your 3 entries already match a baseline pattern and add nothing')
  })

  it('pluralises, and stays silent about shadowing when there is none', async () => {
    const r = await mount({ denied: { user: ['mine-a', 'mine-b'], user_additions: 2 } })
    const text = r.container.textContent ?? ''
    expect(text).toContain('2 user additions on top of the baseline')
    expect(text).not.toContain('already match a baseline pattern')
  })

  it('reads zero as zero', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('0 user additions on top of the baseline')
  })
})

describe('the baseline is read-only in the UI', () => {
  it('the baseline region contains no control that could submit a change', async () => {
    const r = await mount({ denied: { user: ['mine-a'], user_additions: 1 } })
    const region = r.getByRole('group', { name: /^Baseline shell denylist patterns \(112\), read-only$/ })

    for (const role of ['button', 'textbox', 'checkbox', 'combobox', 'switch'] as const) {
      expect(within(region).queryAllByRole(role),
        `a ${role} inside the baseline region would be a write path`).toEqual([])
    }
    expect(region.querySelectorAll('input, button, select, textarea, [contenteditable]').length).toBe(0)

    expect(r.getByRole('button', { name: 'Remove mine-a' })).toBeTruthy()
    expect(r.getByRole('textbox', { name: 'Add a shell denylist pattern (regex)' })).toBeTruthy()
  })

  it('no write path addresses the baseline at all', async () => {
    const src = readFileSync(PANEL, 'utf8')
    expect(src).toContain('api.setUserDeniedCommands(next)')
    expect(src, 'nothing here may write the baseline').not.toMatch(/set\w*Baseline|baseline:\s*\[/)
  })
})

describe('a failed read says so instead of showing an empty denylist', () => {
  it('the denylist failure renders an alert with the server message and a retry', async () => {
    const r = await mount({ denied: 'reject' })

    const alert = r.getByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your shell denylist patterns")
    expect(alert.textContent, "the server's own message must be on the page")
      .toContain('probe-induced 500 on /api/security/denied-commands')
    expect(r.getByRole('button', { name: /Retry/ })).toBeTruthy()
    expect(r.queryByText('deny-pattern-0'), 'and no fabricated list').toBeNull()
  })

  it('the gating stats failure replaces the panel rather than shimmering forever', async () => {
    const r = await mount({ stats: 'reject' })

    const alert = r.getByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your security settings")
    expect(alert.textContent).toContain('probe-induced 500 on /api/security/stats')
    expect(r.container.querySelector('[aria-busy="true"]')).toBeNull()
    expect(r.container.textContent, 'and no fabricated zero counts').not.toContain('Denied commands')
  })

  it('both reads are bare, so the rejection can reach the hook at all', async () => {
    const src = readFileSync(PANEL, 'utf8')
    for (const call of ['api.securityStats()', 'api.deniedCommands()']) {
      const line = src.split('\n').find((l) => l.includes(call)) ?? ''
      expect(line, `${call} must not swallow its rejection`).not.toMatch(/\.catch\(\(\)\s*=>/)
    }
  })
})

describe('each Add button explains ITS OWN empty draft', () => {
  it('the denylist button asks for a pattern and the host button asks for a host', async () => {
    const r = await mount()
    const titles = [...r.container.querySelectorAll('button[title]')].map((b) => b.getAttribute('title'))
    expect(titles.filter((t) => t === 'Enter a pattern first').length, 'one denylist Add').toBe(1)
    expect(titles.filter((t) => t === 'Enter a host first').length, 'two host Adds (allow + deny)').toBe(2)
  })
})

describe('the panel does not overclaim what the digest proves', () => {
  it('says "matches what shipped" and never "tamper-proof" or "secure"', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).not.toMatch(/tamper-proof|tamperproof|cannot be changed|guaranteed/i)
    expect(text).toContain('matches what shipped')
    expect(text, 'the limitation is stated on the surface, not only in docs/')
      .toContain('Anyone who can edit the installed package before Gideon starts owns the baseline')
  })
})
