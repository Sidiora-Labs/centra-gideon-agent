import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SenderTrustPanel } from './SenderTrustPanel'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { closeDialog, subscribeDialogs } from '../../shared/ui/dialog/dialogStore'
import { api } from '../../shared/data/api'
import type { ChannelTrust, ChannelTrustProvider, ChannelTrustSender } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'

function sender(over: Partial<ChannelTrustSender> = {}): ChannelTrustSender {
  return { sender_id: 'u1', name: 'Alice', added_at: '2026-08-01T10:00:00+00:00', via: 'owner', ...over }
}

function provider(over: Partial<ChannelTrustProvider> = {}): ChannelTrustProvider {
  return {
    provider: 'telegram',
    policies: { dm: 'pairing', group: 'tracked_only' },
    allowed_senders: [sender()],
    tracked_channels: [],
    pairing_active: false,
    pairing_expires_at: '',
    ...over,
  }
}

function trust(over: Partial<ChannelTrust> = {}): ChannelTrust {
  return {
    providers: [provider()],
    dm_policies: ['pairing', 'owner_only', 'open'],
    group_policies: ['tracked_only', 'off'],
    default_dm_policy: 'pairing',
    default_group_policy: 'tracked_only',
    ...over,
  }
}

function captureToasts(): string[] {
  const seen: string[] = []
  window.addEventListener('ne:toast', ((e: Event) => {
    seen.push(String((e as CustomEvent).detail?.message ?? ''))
  }) as EventListener)
  return seen
}

const mount = () => render(<><SenderTrustPanel /><DialogHost /></>)

beforeEach(() => {
  invalidateKeys('settings:sender-trust')
})

afterEach(() => {
  let pending: { id: number }[] = []
  subscribeDialogs((list) => { pending = list })()
  for (const d of pending) closeDialog(d.id, false)
  vi.restoreAllMocks()
})

describe('SenderTrustPanel', () => {
  it('lists each trusted sender with its provenance and the channel it applies to', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({
        allowed_senders: [
          sender({ sender_id: '111', name: 'Alice', via: 'owner' }),
          sender({ sender_id: '222', name: '', via: 'pairing' }),
        ],
      })],
    }))
    mount()

    expect(await screen.findByText('Alice')).toBeTruthy()
    expect(screen.getByText(/You allowed them/)).toBeTruthy()
    expect(screen.getByText(/Redeemed a pairing code/)).toBeTruthy()
    expect(screen.getByText('222')).toBeTruthy()
    expect(screen.getByText(/Strangers must redeem a pairing code/)).toBeTruthy()
  })

  it("names the row AND the channel in the revoke control's accessible name", async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [
        provider({ provider: 'telegram', allowed_senders: [sender({ sender_id: 'u1', name: 'Alice' })] }),
        provider({ provider: 'discord', allowed_senders: [sender({ sender_id: 'u1', name: 'Alice' })] }),
      ],
    }))
    mount()

    expect(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Revoke Alice on Discord/i })).toBeTruthy()
  })

  it('names the sender in the confirmation and says what revoking actually does', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))

    const dialog = await screen.findByRole('alertdialog')
    expect(dialog.textContent).toContain('Alice')
    expect(dialog.textContent).toMatch(/allowlist/i)
    expect(dialog.textContent).toMatch(/not interrupted/i)
    expect(revoke).not.toHaveBeenCalled()
  })

  it('writes nothing when the confirmation is dismissed', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/i }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(revoke).not.toHaveBeenCalled()
  })

  it('sends the provider AND sender id on confirm, then RE-READS the list', async () => {
    const list = vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    const revoke = vi.spyOn(api, 'revokeChannelSender').mockResolvedValue(undefined as never)
    const toasts = captureToasts()
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /Revoke access/i }))

    await waitFor(() => expect(revoke).toHaveBeenCalledWith('telegram', 'u1'))
    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(1))
    await waitFor(() => expect(toasts.some((t) => /Alice/.test(t))).toBe(true))
  })

  it("reports a failed revoke with the server's own reason, and keeps the row", async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust())
    vi.spyOn(api, 'revokeChannelSender').mockRejectedValue(
      new Error('That sender is not on this channel’s allowlist.'),
    )
    const toasts = captureToasts()
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Revoke Alice on Telegram/i }))
    await screen.findByRole('alertdialog')
    await userEvent.click(screen.getByRole('button', { name: /Revoke access/i }))

    await waitFor(() => expect(toasts.some((t) => /not on this channel/i.test(t))).toBe(true))
    expect(screen.getByRole('button', { name: /Revoke Alice on Telegram/i })).toBeTruthy()
  })

  it('renders a load error instead of an empty allowlist when the read fails', async () => {
    vi.spyOn(api, 'channelTrust').mockRejectedValue(new Error('boom'))
    mount()

    expect(await screen.findByText(/Couldn't load your sender trust/i)).toBeTruthy()
    expect(screen.queryByText(/No channel has any trust state yet/i)).toBeNull()
    expect(screen.queryByText(/No trusted senders/i)).toBeNull()
  })

  it('says a channel has nobody trusted without claiming the read failed', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ allowed_senders: [] })],
    }))
    mount()

    expect(await screen.findByText(/Nobody is trusted on Telegram/i)).toBeTruthy()
    expect(screen.queryByText(/Couldn't load/i)).toBeNull()
  })

  it('surfaces an outstanding pairing code without ever showing a code', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ pairing_active: true, pairing_expires_at: '2026-08-01T10:10:00+00:00' })],
    }))
    const { container } = mount()

    expect(await screen.findByText(/A pairing code is outstanding for Telegram/i)).toBeTruthy()
    expect(container.textContent ?? '').not.toMatch(/\b\d{8}\b/)
  })

  it('reads an unrecognized provider and provenance as itself, never as blank', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ provider: 'matrix', allowed_senders: [sender({ via: 'imported' })] })],
    }))
    mount()

    expect(await screen.findByRole('button', { name: /Revoke Alice on matrix/i })).toBeTruthy()
    expect(screen.getByText(/imported/)).toBeTruthy()
  })

  it('reads a missing added_at as unknown rather than as today', async () => {
    vi.spyOn(api, 'channelTrust').mockResolvedValue(trust({
      providers: [provider({ allowed_senders: [sender({ added_at: '' })] })],
    }))
    mount()

    expect(await screen.findByText(/date unknown/i)).toBeTruthy()
  })
})
