import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../shared/data/data'
import type { PendingApproval } from '../../shared/data/api'

vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))

const approvals = vi.fn()
const resolveApproval = vi.fn()
vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals: () => approvals(),
      resolveApproval: (id: string, action: string) => resolveApproval(id, action),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxPending: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
    },
  }
})

const AP: PendingApproval = {
  id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'rm -rf /tmp/scratch', tool_purpose: 'Clear the scratch directory',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90,
}

const route = { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }

beforeEach(() => {
  approvals.mockReset()
  resolveApproval.mockReset()
  invalidateKeys('companion:', true)
  for (const k of ['loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']) invalidateKeys(k)
  sessionStorage.clear()
})
afterEach(cleanup)

describe('the companion approvals queue', () => {
  it('renders the full decision context from GET /api/approvals', async () => {
    approvals.mockResolvedValue([AP])
    render(<CompanionPage {...route} />)
    const card = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    expect(card.textContent).toContain('Bash')
    expect(card.textContent).toContain('rm -rf /tmp/scratch')
    expect(card.textContent).toContain('Clear the scratch directory')
    expect(card.textContent).toContain('cron:nightly')
    expect(card.textContent).toContain('cron')
    expect(card.querySelector('[role="alert"]')?.textContent).toBe('Permission needed')
  })

  it('pretty-prints a structured tool_input instead of "[object Object]"', async () => {
    approvals.mockResolvedValue([{ ...AP, tool_input: { command: 'ls', cwd: '/tmp' } }])
    render(<CompanionPage {...route} />)
    const card = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    expect(card.textContent).toContain('"command": "ls"')
    expect(card.textContent).not.toContain('[object Object]')
  })

  it('approve round-trips to the queue resolver, not the chat route', async () => {
    approvals.mockResolvedValueOnce([AP]).mockResolvedValue([])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Allow Bash' })).toBeNull())
  })

  it('brings a card BACK if the server still lists it after the answer settled', async () => {
    approvals.mockResolvedValueOnce([{ ...AP }]).mockResolvedValue([{ ...AP }])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Allow Bash' })).toBeTruthy())
  })

  it('reject round-trips with the reject action', async () => {
    approvals.mockResolvedValue([AP])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Deny Bash' }))
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'reject')
  })

  it('puts a row BACK and announces when the resolve call fails', async () => {
    approvals.mockResolvedValue([AP])
    resolveApproval.mockRejectedValue(new Error('gateway unreachable'))
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push(String((e as CustomEvent).detail?.message ?? ''))
    window.addEventListener('ne:toast', onToast)
    try {
      render(<CompanionPage {...route} />)
      await userEvent.click(await screen.findByRole('button', { name: 'Allow Bash' }))
      await waitFor(() => expect(toasts.join('|')).toContain('gateway unreachable'))
      expect(toasts.join('|')).toContain("Couldn't approve Bash")
      await waitFor(() => expect(screen.getByRole('button', { name: 'Allow Bash' })).toBeTruthy())
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it('TELLS the user when the queue could not be loaded — never "nothing waiting on you"', async () => {
    approvals.mockRejectedValue(new Error('probe-induced failure'))
    render(<CompanionPage {...route} />)
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain("Couldn't load your approvals")
    expect(alert.textContent).toContain('probe-induced failure')
    expect(screen.queryByText('Nothing waiting on you')).toBeNull()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it('says "nothing waiting" only when the fetch SUCCEEDED and was empty', async () => {
    approvals.mockResolvedValue([])
    render(<CompanionPage {...route} />)
    expect(await screen.findByText('Nothing waiting on you')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('names every control unambiguously, even with two approvals pending', async () => {
    approvals.mockResolvedValue([AP, { ...AP, id: 'ap-2', tool: 'WebFetch', session: '' }])
    render(<CompanionPage {...route} />)
    await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') || b.textContent?.trim() || '')
    expect(names.every((n) => n.length > 0)).toBe(true)
    expect(new Set(names).size, `duplicate accessible names: ${names.join(', ')}`).toBe(names.length)
    expect(names).toContain('Allow Bash')
    expect(names).toContain('Deny WebFetch')
  })

  it('is fully keyboard operable — a mobile-width surface is not keyboard-exempt', async () => {
    approvals.mockResolvedValue([AP])
    resolveApproval.mockResolvedValue({ ok: true })
    render(<CompanionPage {...route} />)
    const allow = await screen.findByRole('button', { name: 'Allow Bash' })
    allow.focus()
    expect(document.activeElement).toBe(allow)
    await userEvent.keyboard('{Enter}')
    expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve')
  })

  it('keeps the approvals queue FIRST once the other sections are on the page', async () => {
    approvals.mockResolvedValue([])
    render(<CompanionPage {...route} />)
    await screen.findByText('Nothing waiting on you')
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)[0]).toBe('Approvals')
    expect(screen.queryByText('Not on the phone yet')).toBeNull()
  })
})

describe('the route is registered under the URL doctrine', () => {
  const app = readFileSync(join(process.cwd(), "src/app/shell/App.tsx"), 'utf8')

  it("App.tsx serves `#/companion` and does NOT fall through to the dashboard", () => {
    expect(app).toContain("route === 'companion'")
    expect(app).toMatch(/<CompanionPage\s/)
  })

  it('stays out of NAV and ROUTABLE — a deep link, not a desktop nav tile', () => {
    const nav = app.match(/const NAV: NavItem\[\] = \[(.*?)\n\]/s)?.[1] ?? ''
    expect(nav.length, 'NAV literal must parse or this assertion is vacuous').toBeGreaterThan(100)
    expect(nav).not.toContain('companion')
    const routable = app.match(/const ROUTABLE = new Set\(\[(.*?)\]\)/s)?.[1] ?? ''
    expect(routable.length, 'ROUTABLE literal must parse').toBeGreaterThan(10)
    expect(routable).not.toContain('companion')
  })
})
