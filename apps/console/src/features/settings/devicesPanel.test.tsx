import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DevicesPanel } from './DevicesPanel'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../shared/ui/dialog/dialogStore'
import { invalidateKeys } from '../../shared/data/data'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type DeviceRec, type DevicePairStart } from '../../shared/data/api'
import { encodeQr, qrPath } from '../../shared/data/qr'


function device(over: Partial<DeviceRec> = {}): DeviceRec {
  return {
    id: 'dev-1',
    name: 'Kitchen tablet',
    kind: 'mobile',
    minted_at: 1_786_500_000,
    last_seen: 0,
    issuer: 'pair',
    expires_at: 1_790_000_000,
    ...over,
  }
}

const START: DevicePairStart = {
  code: 'ABCD-EFGH',
  pairing_url: 'http://192.168.1.5:10000/pair?code=ABCD-EFGH',
  expires_at: Math.floor(Date.now() / 1000) + 300,
  expires_in: 300,
}

function captureToasts(): string[] {
  const seen: string[] = []
  window.addEventListener('ne:toast', ((e: Event) => {
    seen.push(String((e as CustomEvent).detail?.message ?? ''))
  }) as EventListener)
  return seen
}

const mount = () => render(<><DevicesPanel /><DialogHost /></>)

beforeEach(() => {
  invalidateKeys('settings:devices')
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('the registry shows every column the owner needs', () => {
  it('renders name, kind, last-seen, issuer and the paired/expiry line', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device({ last_seen: 1_786_600_000 })])
    mount()

    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    const meta = screen.getByText(/Last seen/).parentElement?.textContent ?? ''
    expect(meta, 'the kind, as a word not just a glyph').toMatch(/Phone/)
    expect(meta, 'the last-seen column').toMatch(/Last seen/)
    expect(meta, 'the issuer, in the owner’s words').toMatch(/Paired with a code/)
    expect(screen.getByText(/^Paired \d+[mhd] ago/), 'and when it paired').toBeTruthy()
    expect(screen.getByText(/session expires/), 'and when the session runs out').toBeTruthy()
  })

  it('a device that never came back reads "never" — NOT its pairing time', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device({ last_seen: 0 })])
    mount()

    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    expect(screen.getByText(/Last seen never/i), 'an unstamped device is "never"').toBeTruthy()
    expect(screen.queryByText(/Last seen \d+[mhd] ago/i), 'a backfill from minted_at').toBeNull()
    expect(screen.queryByText(/Last seen just now/i)).toBeNull()
  })

  it('a stamped device reads as a relative time, so "never" is not the only branch', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([
      device({ last_seen: Math.floor(Date.now() / 1000) - 180 }),
    ])
    mount()
    await waitFor(() => expect(screen.getByText(/Last seen 3m ago/i)).toBeTruthy())
    expect(screen.queryByText(/Last seen never/i)).toBeNull()
  })

  it('an unnamed device still has something to call it', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([device({ name: '' })])
    mount()
    await waitFor(() => expect(screen.getByText('Unnamed device')).toBeTruthy())
    expect(screen.getByRole('button', { name: /Revoke Unnamed device/i })).toBeTruthy()
  })
})

describe('revoking is confirmed, named, and never silent', () => {
  it('the confirmation NAMES the device it is about to lock out', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent ?? '').toMatch(/Kitchen tablet/)
    expect(revoke, 'asking is not doing').not.toHaveBeenCalled()
  })

  it('a DISMISSED confirmation revokes nothing', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const cancel = Array.from(dialog.querySelectorAll('button')).find((b) => /cancel/i.test(b.textContent ?? ''))
    expect(cancel).toBeTruthy()
    fireEvent.click(cancel!)

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(revoke).not.toHaveBeenCalled()
    expect(screen.getByText('Kitchen tablet'), 'and the device is still listed').toBeTruthy()
  })

  it('a CONFIRMED revoke sends that device id and re-reads the list', async () => {
    const revoke = vi.spyOn(api, 'deviceRevoke').mockResolvedValue({ ok: true, revoked: 1 })
    const list = vi.spyOn(api, 'devices')
    list.mockResolvedValueOnce([device()]).mockResolvedValue([])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /revoke access/i.test(b.textContent ?? ''))
    fireEvent.click(go!)

    await waitFor(() => expect(revoke).toHaveBeenCalledWith('dev-1'))
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
  })

  it('a FAILED revoke is REPORTED, and the device stays listed', async () => {
    const toasts = captureToasts()
    vi.spyOn(api, 'deviceRevoke').mockRejectedValue(new Error('device store is read-only'))
    vi.spyOn(api, 'devices').mockResolvedValue([device()])
    mount()
    await waitFor(() => expect(screen.getByText('Kitchen tablet')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Revoke Kitchen tablet/i }))
    const dialog = await screen.findByRole('alertdialog')
    const go = Array.from(dialog.querySelectorAll('button')).find((b) => /revoke access/i.test(b.textContent ?? ''))
    fireEvent.click(go!)

    await waitFor(() => expect(toasts.some((t) => /Couldn't revoke Kitchen tablet/i.test(t))).toBe(true))
    expect(toasts.join(' ')).toMatch(/read-only/)
    expect(screen.getByText('Kitchen tablet'), 'still there, because it still has access').toBeTruthy()
  })
})

describe('an empty registry and a failed read are different answers', () => {
  it('says nothing is paired, honestly, and offers the way to change that', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    expect(screen.getByRole('button', { name: /^Pair a device$/i }), 'the section control').toBeTruthy()
    expect(screen.getByRole('button', { name: /Pair your first device/i }), 'the empty-state on-ramp').toBeTruthy()
  })

  it('the empty state’s on-ramp actually opens pairing', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    const start = vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Pair your first device/i }))
    await waitFor(() => expect(start).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
  })

  it('a FAILED read renders the error, never the empty state', async () => {
    vi.spyOn(api, 'devices').mockRejectedValue(new Error('devices unreadable'))
    mount()
    await waitFor(() => expect(screen.getByText(/devices unreadable/)).toBeTruthy())
    expect(screen.queryByText(/No devices paired/i)).toBeNull()
  })
})

describe('pairing surfaces the code and the link', () => {
  it('shows the code and the actionable URL, with the expiry counting down', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    expect(screen.getByText(START.pairing_url)).toBeTruthy()
    expect(screen.getByText(/Expires in \d+:\d\d/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Copy pairing code/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Copy pairing link/i })).toBeTruthy()
  })

  it('renders a QR of the PAIRING URL — the payload, not the bare code (MC-8)', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    const svg = screen.getByRole('img', { name: /scan it with the camera/i })
    const drawn = svg.querySelector('path')?.getAttribute('d') ?? ''
    const symbol = encodeQr(START.pairing_url)!
    expect(drawn, 'the QR encodes the pairing URL').toBe(qrPath(symbol))
    expect(drawn, 'and NOT the bare code').not.toBe(qrPath(encodeQr(START.code)!))
    expect([...drawn.matchAll(/h1v1h-1z/g)].length).toBeGreaterThan(200)
    expect(svg.getAttribute('viewBox')).toBe(`0 0 ${symbol.size + 8} ${symbol.size + 8}`)
  })

  it('an EXPIRED code WITHDRAWS the payload instead of offering a dead one', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue({
      ...START, expires_at: Math.floor(Date.now() / 1000) - 5, expires_in: 0,
    })
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText(/This code has expired/i)).toBeTruthy())
    expect(screen.queryByText(/Expires in/i), 'not both at once').toBeNull()
    expect(screen.getByRole('button', { name: /Generate a new pairing code/i })).toBeTruthy()
    expect(screen.queryByRole('img', { name: /scan it with the camera/i })).toBeNull()
    expect(screen.queryByText('ABCD-EFGH'), 'the code is gone').toBeNull()
    expect(screen.queryByText(START.pairing_url), 'the link is gone').toBeNull()
    expect(screen.queryByRole('button', { name: /Copy pairing code/i })).toBeNull()
    expect(screen.getByText(/refuses an expired code/i)).toBeTruthy()
  })

  it('a gateway that cannot resolve its own address refuses the QR and keeps the code', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue({ ...START, pairing_url: '' })
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    expect(screen.queryByRole('img', { name: /scan it with the camera/i })).toBeNull()
    expect(screen.getByText(/could not work out its own address/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Copy pairing code/i })).toBeTruthy()
  })

  it('a FAILED pair/start is reported, not swallowed into a blank panel', async () => {
    const toasts = captureToasts()
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockRejectedValue(new Error('too many outstanding codes'))
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])

    await waitFor(() => expect(toasts.some((t) => /Couldn't start pairing/i.test(t))).toBe(true))
    expect(toasts.join(' ')).toMatch(/too many outstanding codes/)
  })
})


describe('the pairing flow says what happened, and keeps your place', () => {
  it('announces the code once, through an always-mounted region', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    const { container } = mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())

    const region = () => container.querySelector('[role="status"][aria-live="polite"].sr-only')
    expect(region(), 'the announcement region must exist before the event').toBeTruthy()
    expect(region()!.textContent, 'and say nothing while idle').toBe('')

    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())
    await waitFor(() => expect(region()!.textContent).toMatch(/Pairing code ABCD-EFGH is ready/))
    expect(region()!.textContent, 'and it says how long it lasts, in words').toMatch(/expires in about 5 minutes/)
  })

  it('the countdown is NOT a live region — a ticking value is not an event', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    const { container } = mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText(/Expires in/)).toBeTruthy())

    const ticking = screen.getByText(/Expires in/)
    expect(ticking.getAttribute('role'), 'six announcements in six seconds is not an announcement')
      .not.toBe('status')
    expect(ticking.closest('[aria-live]'), 'nor may an ancestor make it live').toBeNull()
    expect(ticking.textContent).toMatch(/Expires in \d+:\d\d/)
    expect(container.querySelectorAll('[role="status"]').length).toBe(1)
  })

  it('focus moves to the code, on a named group that is not in the tab order', async () => {
    vi.spyOn(api, 'devices').mockResolvedValue([])
    vi.spyOn(api, 'devicePairStart').mockResolvedValue(START)
    mount()
    await waitFor(() => expect(screen.getByText(/No devices paired/i)).toBeTruthy())
    fireEvent.click(screen.getAllByRole('button', { name: /Pair a device/i })[0])
    await waitFor(() => expect(screen.getByText('ABCD-EFGH')).toBeTruthy())

    const group = screen.getByRole('group', { name: 'Pairing code and link' })
    await waitFor(() => expect(document.activeElement).toBe(group))
    expect(group.getAttribute('tabindex'), 'a programmatic target, not a new tab stop').toBe('-1')
    expect(group.contains(screen.getByText('ABCD-EFGH')), 'and it must actually contain the code').toBe(true)
  })

  it('the hooks that do this run before the panel can bail out', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/DevicesPanel.tsx"), 'utf8')
    const firstEffectForCode = src.indexOf('if (announce.current === pairing.code) return')
    const firstEarlyReturn = src.indexOf('if (!data && loadErr) return')
    expect(firstEffectForCode, 'the announce/focus effect must exist').toBeGreaterThan(-1)
    expect(firstEarlyReturn, 'the loading guard must exist').toBeGreaterThan(-1)
    expect(firstEffectForCode, 'hooks before guards, always').toBeLessThan(firstEarlyReturn)
  })

  it('the announcement is keyed on the CODE, so a tick cannot re-announce or steal focus', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/DevicesPanel.tsx"), 'utf8')
    expect(src, 'guarded on the code it already announced').toContain('if (announce.current === pairing.code) return')
    expect(src, 'and the effect depends on the pairing object, not on the countdown').toMatch(/\}, \[pairing\]\)/)
    expect(src, 'the countdown state must NOT be a dependency of the focus effect').not.toMatch(/\}, \[pairing, left\]\)/)
  })
})
