import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { BrowseExpiredSite, BrowseKillState, BrowseStatus } from '../../../shared/data/api'
import type { WsMessage } from '../../../shared/data/socketTransport'

const NO_KILL: BrowseKillState = { active: false, reason: '', started_at: '' }
const STARTED_AT = '2026-01-02T03:04:05+00:00'

let kill: BrowseKillState = NO_KILL
let expired: BrowseExpiredSite[] = []

const browseStatus = vi.fn<() => Promise<BrowseStatus>>()
const browseKill = vi.fn<(reason: string) => Promise<{ kill: BrowseKillState }>>()
const browseKillRelease = vi.fn<() => Promise<{ kill: BrowseKillState }>>()

vi.mock('../../../shared/data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../../shared/data/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      browseStatus: () => browseStatus(),
      browseKill: (reason: string) => browseKill(reason),
      browseKillRelease: () => browseKillRelease(),
    },
  }
})

let socket: ((message: WsMessage) => void) | null = null
vi.mock('../../../shared/data/useChatSocket', () => ({
  useChatSocket: (onMessage: (message: WsMessage) => void) => { socket = onMessage },
}))

import { BrowseMirrorPanel } from './BrowseMirrorPanel'
import { DialogHost } from '../../../shared/ui/dialog/DialogHost'
import { readExpiredSite, readStepFrame, resetBrowseMirror } from './browseMirrorState'
import { resetDataStore } from '../../../shared/data/data'

const SHOT = '/home/me/.gideon/browse/shots/run-7-3.png'
const STEP = {
  run_id: 'run-7',
  step_n: 3,
  url: 'https://docs.example.com/pricing',
  action: 'click "Compare plans"',
  screenshot: SHOT,
  note: 'page loaded',
}

const send = (type: string, data: Record<string, unknown>) => act(() => { socket?.({ type, data }) })

const mount = () => render(<><BrowseMirrorPanel /><DialogHost /></>)

const signedOut = (site: string) => new RegExp(`Sign-in expired for ${site.replace(/\./g, '\\.')}`)

beforeEach(() => {
  socket = null
  kill = NO_KILL
  expired = []
  resetBrowseMirror()
  resetDataStore()
  browseStatus.mockReset().mockImplementation(async () => ({ kill, expired: [...expired] }))
  browseKill.mockReset().mockImplementation(async (reason: string) => {
    kill = { active: true, reason, started_at: STARTED_AT }
    return { kill }
  })
  browseKillRelease.mockReset().mockImplementation(async () => {
    kill = NO_KILL
    return { kill }
  })
})
afterEach(() => cleanup())

describe('the browser automation panel reads the status route', () => {
  it('renders the kill posture and the expired sign-ins the payload carries', async () => {
    kill = { active: true, reason: 'that page looked wrong', started_at: STARTED_AT }
    expired = [{ site: 'mail.example.com', key_present: true }]
    mount()
    await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
    expect(screen.getByText('that page looked wrong')).toBeTruthy()
    expect(screen.getByText(signedOut('mail.example.com'))).toBeTruthy()
    expect(screen.getByText(/the saved profile is reused/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Resume browsing' })).toBeTruthy()
  })

  it('a failed read says so instead of claiming a quiet browser', async () => {
    browseStatus.mockRejectedValue(new Error('boom'))
    mount()
    await waitFor(() =>
      expect(screen.getByText(/couldn’t read the browser automation status/i)).toBeTruthy())
  })
})

describe('the live frames update the view', () => {
  it('a browse_step frame shows the address, the latest action and the screenshot reference', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())
    expect(screen.getByText(/No browse step has been mirrored yet/)).toBeTruthy()

    await send('browse_step', STEP)

    expect(screen.getByText('https://docs.example.com/pricing')).toBeTruthy()
    expect(screen.getByText(/Step 3: click "Compare plans"/)).toBeTruthy()
    const shot = screen.getByRole('link', { name: 'run-7-3.png' })
    expect(shot.getAttribute('href')).toBe(`/api/file-raw?path=${encodeURIComponent(SHOT)}`)

    await send('browse_step', {
      ...STEP, step_n: 4, url: 'https://docs.example.com/checkout', action: 'fill "Email"', screenshot: '',
    })
    expect(screen.getByText('https://docs.example.com/checkout')).toBeTruthy()
    expect(screen.queryByText('https://docs.example.com/pricing')).toBeNull()
    expect(screen.getByText('not captured for this step')).toBeTruthy()
  })

  it('a browse_kill frame flips the posture without waiting for a poll', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())
    await send('browse_kill', { active: true, reason: 'stopped from the CLI', started_at: STARTED_AT })
    expect(screen.getByText('Stopped')).toBeTruthy()
    expect(screen.getByText('stopped from the CLI')).toBeTruthy()
  })
})

describe('the emergency stop and its confirm-gated resume', () => {
  it('the stop button engages the kill switch', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Emergency stop' }))
    await waitFor(() => expect(browseKill).toHaveBeenCalledTimes(1))
    expect(browseKill.mock.calls[0][0]).toBe('Emergency stop from the dashboard')
    await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
  })

  it('a declined confirmation releases NOTHING', async () => {
    kill = { active: true, reason: 'that page looked wrong', started_at: STARTED_AT }
    mount()
    await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Resume browsing' }))
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: 'Cancel' })))
    await waitFor(() => expect(screen.queryByText('Resume automated browsing?')).toBeNull())
    expect(browseKillRelease).not.toHaveBeenCalled()
    expect(screen.getByText('Stopped')).toBeTruthy()
  })

  it('an accepted confirmation releases it', async () => {
    kill = { active: true, reason: 'that page looked wrong', started_at: STARTED_AT }
    mount()
    await waitFor(() => expect(screen.getByText('Stopped')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Resume browsing' }))
    const go = await waitFor(() => screen.getByRole('button', { name: 'Resume unattended browsing' }))
    expect(browseKillRelease).not.toHaveBeenCalled()
    fireEvent.click(go)
    await waitFor(() => expect(browseKillRelease).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())
  })
})

describe('an authentication-expiry notice persists', () => {
  it('survives a later frame and an unmount/remount with no further status read', async () => {
    mount()
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())

    expired = [{ site: 'mail.example.com', key_present: true }]
    await send('browse_auth_expired', { site: 'mail.example.com' })
    await waitFor(() => expect(screen.getByText(signedOut('mail.example.com'))).toBeTruthy())

    expired = [...expired, { site: 'intranet.example.org', key_present: false }]
    await send('browse_auth_expired', { site: 'intranet.example.org' })
    await waitFor(() => expect(screen.getByText(signedOut('intranet.example.org'))).toBeTruthy())

    await send('browse_step', STEP)
    expect(screen.getByText(signedOut('mail.example.com'))).toBeTruthy()
    expect(screen.getByText(signedOut('intranet.example.org'))).toBeTruthy()

    cleanup()
    browseStatus.mockImplementation(() => new Promise<BrowseStatus>(() => {}))
    mount()
    expect(screen.getByText(signedOut('mail.example.com'))).toBeTruthy()
    expect(screen.getByText(signedOut('intranet.example.org'))).toBeTruthy()
  })
})

describe('no credential value reaches the panel', () => {
  it('a credential-shaped field in a frame is projected away, never rendered', async () => {
    const { container } = mount()
    await waitFor(() => expect(screen.getByText('Browsing allowed')).toBeTruthy())

    expired = [{ site: 'mail.example.com', key_present: true }]
    await send('browse_step', {
      ...STEP, password: 'hunter2', cookie: 'session=abcdef', authorization: 'Bearer tok-123',
    })
    await send('browse_auth_expired', {
      site: 'mail.example.com', password: 'hunter2', profile_key: 'k-9f3', token: 'tok-123',
    })
    await waitFor(() => expect(screen.getByText(signedOut('mail.example.com'))).toBeTruthy())

    for (const secret of ['hunter2', 'session=abcdef', 'Bearer tok-123', 'k-9f3', 'tok-123']) {
      expect(container.innerHTML).not.toContain(secret)
      expect(document.body.textContent ?? '').not.toContain(secret)
    }

    expect(readStepFrame({ ...STEP, password: 'hunter2' })).toEqual(STEP)
    expect(readExpiredSite({ site: 'mail.example.com', profile_key: 'k-9f3' })).toEqual({ site: 'mail.example.com' })
  })
})
