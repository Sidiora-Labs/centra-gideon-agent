import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent, within } from '@testing-library/react'

const ITEM = {
  ts: 1785890207, kind: 'error', title: 'Inventory agent hit an error',
  body: 'boom', acked: true, mode: '', targets: [],
}

const deleteMock = vi.fn(async (..._a: unknown[]): Promise<unknown> => ({}))
const notified: string[] = []

vi.mock('../data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../data/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      notifications: vi.fn(async () => ({ notifications: [ITEM] })),
      deleteNotification: (...a: unknown[]) => deleteMock(...a),
      ackNotification: vi.fn(async () => ({})),
      ackAllNotifications: vi.fn(async () => ({})),
    },
  }
})
vi.mock('../data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../app/shell/appSdk', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  notify: (m: string) => { notified.push(m) },
}))

import { NotificationBell } from './NotificationBell'
import { DialogHost } from './dialog/DialogHost'

function mount() {
  return render(<><NotificationBell navigate={() => {}} /><DialogHost /></>)
}

const DELETE_NAME = /^Delete: /

async function openShadeAndFindDelete() {
  mount()
  fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Notifications/ })))
  return waitFor(() => screen.getByRole('button', { name: DELETE_NAME }))
}

beforeEach(() => { deleteMock.mockClear(); notified.length = 0 })
afterEach(() => cleanup())

describe('the bell row calls its delete a delete', () => {
  it('is named "Delete", not "Dismiss"', async () => {
    mount()
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Notifications/ })))
    const btn = await waitFor(() => screen.getByRole('button', { name: DELETE_NAME }))
    expect(btn.getAttribute('aria-label'), 'and it names which row').toContain(ITEM.title)
    expect(screen.queryByRole('button', { name: new RegExp(`Dismiss`, 'i') })).toBeNull()
  })

  it('names WHICH notification, since a five-row shade looks alike', async () => {
    const btn = await openShadeAndFindDelete()
    expect(btn.getAttribute('aria-label')).toMatch(DELETE_NAME)
    expect(btn.getAttribute('aria-label'), 'carrying the row it belongs to').toContain(ITEM.title)
    expect(btn.getAttribute('title')).toBe('Delete')
  })
})

describe('it confirms before the entry leaves disk', () => {
  it('a declined confirm deletes NOTHING', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    const cancel = await waitFor(() => screen.getByRole('button', { name: /cancel/i }))
    fireEvent.click(cancel)
    await waitFor(() => expect(screen.queryByRole('button', { name: /cancel/i })).toBeNull())
    expect(deleteMock, 'declining must not reach the API').not.toHaveBeenCalled()
  })

  it('the dialog names the notification and says it cannot be undone', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    // global `getByText` matches two nodes and throws — the assertion has to say which surface it
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    expect(within(dlg).getByText(new RegExp(`Delete notification "${ITEM.title}`))).toBeInTheDocument()
    expect(within(dlg).getByText(/cannot be undone/i)).toBeInTheDocument()
    fireEvent.click(within(dlg).getByRole('button', { name: /cancel/i }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  })

  it('a confirmed delete calls the API exactly once', async () => {
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(deleteMock).toHaveBeenCalledTimes(1))
    expect(deleteMock).toHaveBeenCalledWith(ITEM.ts)
  })

  it('🪤 a FAILED delete reports and does not silently re-render the same row', async () => {
    deleteMock.mockRejectedValueOnce(new Error('disk is read-only'))
    const btn = await openShadeAndFindDelete()
    fireEvent.click(btn)
    const dlg = await waitFor(() => screen.getByRole('alertdialog'))
    fireEvent.click(within(dlg).getByRole('button', { name: /^delete$/i }))
    await waitFor(() => expect(notified.length).toBeGreaterThan(0))
    expect(notified[0]).toBe("Couldn't delete this notification: disk is read-only")
    expect(screen.getByRole('button', { name: DELETE_NAME })).toBeInTheDocument()
  })
})

describe('structurally: the bell now matches the sibling #628 fixed', () => {
  it('gates on confirmDelete and reports through reportingWrite, with a gated tail', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const code = readFileSync(join(process.cwd(), "src/shared/ui/NotificationBell.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
    const body = code.match(/async function remove\(n: NotificationItem\)[\s\S]*?\n  \}/)?.[0] ?? ''
    expect(body, 'found remove()').not.toBe('')
    expect(body, 'confirms first').toMatch(/if \(!\(await confirmDelete\('notification'/)
    expect(body, 'reports through the shared owner').toMatch(/await reportingWrite\('delete this notification'/)
    expect(body.indexOf('confirmDelete'), 'confirm precedes the write').toBeLessThan(body.indexOf('reportingWrite'))
    expect(body.indexOf('reportingWrite'), 'and the reload comes last').toBeLessThan(body.indexOf('load()'))
    expect(body, 'the hand-rolled sentence is gone').not.toMatch(/notify\(/)
  })

  it('🔑 no notification surface calls a disk delete a "dismiss"', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    for (const rel of ['src/shared/ui/NotificationBell.tsx', 'src/features/notifications/NotificationsPage.tsx']) {
      const code = readFileSync(join(process.cwd(), rel), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${rel} deletes notifications`).toMatch(/deleteNotification/)
      expect(code, `${rel} must not name a delete "dismiss"`).not.toMatch(/(aria-label|title)=\{?["'`]?[^"'`}]*[Dd]ismiss/)
    }
  })
})
