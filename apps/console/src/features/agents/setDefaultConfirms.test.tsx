import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'


const catalog = {
  agents: [
    { name: 'scout', model: 'x' },
    { name: 'probe', model: 'y' },
  ],
  default_agent: 'scout',
}

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: () => Promise.resolve(catalog),
      agentProviders: () => Promise.resolve([]),
      syncAgents: () => Promise.resolve({ ok: true }),
      setDefaultAgent: setDefaultSpy,
      ...over,
    },
  }))
}

let setDefaultSpy = vi.fn()

async function mountOpenProbe() {
  const { AgentsListPage } = await import('./AgentsListPage')
  const { DialogHost } = await import('../../shared/ui/dialog/DialogHost')
  render(
    <>
      <AgentsListPage query={{ open: 'native:probe' }} setQuery={() => {}} onCreate={() => {}} />
      <DialogHost />
    </>,
  )
  return await screen.findByRole('button', { name: /Set default/i })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  setDefaultSpy = vi.fn().mockResolvedValue({ ok: true, default_agent: 'probe' })
})

afterEach(async () => {
  const { subscribeDialogs, closeDialog } = await import('../../shared/ui/dialog/dialogStore')
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list as { id: number }[] })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('Set default confirms before rewriting the global default (#666)', () => {
  it('the dialog names BOTH agents — the replacement and the one being replaced', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toContain('probe')
    expect(dialog.textContent).toContain('scout')
    expect(setDefaultSpy).not.toHaveBeenCalled()
  })

  it('dismissing writes nothing', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    await screen.findByRole('dialog')
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(setDefaultSpy).not.toHaveBeenCalled()
  })

  it('confirming writes exactly once', async () => {
    mockApi()
    const btn = await mountOpenProbe()
    await userEvent.click(btn)
    const dialog = await screen.findByRole('dialog')
    const { within } = await import('@testing-library/react')
    await userEvent.click(within(dialog).getByRole('button', { name: /^Set default$/i }))
    await waitFor(() => expect(setDefaultSpy).toHaveBeenCalledTimes(1))
    expect(setDefaultSpy).toHaveBeenCalledWith('probe')
  })
})
