import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'



const ENVELOPE = { enabled: true, health: {} }

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      inboxStatus: () => Promise.resolve(ENVELOPE),
      inbox: () => Promise.resolve([]),
      proactiveDigest: () => Promise.resolve({ installed: false }),
      ...over,
    },
  }))
}

async function renderInbox(navigate = vi.fn(), query: Record<string, string> = {}) {
  const { InboxPage } = await import('./InboxPage')
  render(<InboxPage query={query} setQuery={() => {}} navigate={navigate} />)
  return navigate
}

describe('the inbox empty state tells a new user the truth about their setup', () => {
  beforeEach(() => { vi.resetModules() })

  it('with NO source connected it does not say "Inbox zero"', async () => {
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox is not connected yet')).toBeInTheDocument())
    expect(screen.queryByText('Inbox zero'), 'an unconfigured inbox has no zero to be at').toBeNull()
  })

  it('and it offers a way to get there instead of only naming it in prose', async () => {
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    const navigate = await renderInbox()
    const cta = await screen.findByRole('button', { name: /Connect a source/i })
    await userEvent.click(cta)
    expect(navigate, 'the CTA must reach the section the hint names').toHaveBeenCalledWith('settings/inbox')
  })

  it('with a source connected "Inbox zero" survives — it is TRUE there', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.queryByText('Inbox is not connected yet')).toBeNull()
  })

  it('and the connected-empty state offers NO action — good news is not a task', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Connect a source/i }),
      'manufacturing a CTA out of success is what the empty-state taxonomy forbids').toBeNull()
  })

  it('a NARROWED search keeps "Nothing here" and gets no connect action either', async () => {
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    await renderInbox(vi.fn(), { q: 'zzqqxnomatch' })
    await waitFor(() => expect(screen.getByText('Nothing here')).toBeInTheDocument())
    expect(screen.queryByText('Inbox is not connected yet')).toBeNull()
    expect(screen.queryByRole('button', { name: /Connect a source/i }),
      'a no-match filter is not a setup problem').toBeNull()
  })
})

describe('the source still says what the DOM tests rely on', () => {
  const src = readFileSync(join(process.cwd(), "src/features/inbox/InboxPage.tsx"), 'utf8')

  it('the title branches on `disabled`, not only on `narrowed`', () => {
    expect(src, 'the title must distinguish not-connected from caught-up')
      .toMatch(/title=\{narrowed \? 'Nothing here' : disabled \? 'Inbox is not connected yet' : 'Inbox zero'\}/)
  })

  it('the action is gated on BOTH flags', () => {
    expect(src).toMatch(/action=\{disabled && !narrowed/)
  })

  it('the icon is not the glyph this product assigns to Providers', () => {
    const action = src.match(/action=\{disabled && !narrowed[\s\S]{0,240}?\}/)?.[0] ?? ''
    expect(action, 'the action block must be found before it can be checked').not.toBe('')
    expect(action).not.toMatch(/icon:\s*Plug\b/)
  })
})
