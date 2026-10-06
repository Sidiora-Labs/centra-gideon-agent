import { useState } from 'react'
import { MessageCircle, ShieldCheck, KeyRound, UserCheck } from 'lucide-react'
import { api } from '../../shared/data/api'
import type { ChannelTrustProvider, ChannelTrustSender } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { confirm } from '../../shared/ui/dialog'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup } from './settingsUI'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton, ListRow, LoadError } from '../../shared/ui/ListScaffold'
import { channelPerson } from './channelPerson'
import { ChannelOwnerSection } from './ChannelOwnerSection'
import { ApprovalChannelSection } from './ApprovalChannelSection'

const CACHE_KEY = 'settings:sender-trust'

const msg = (e: unknown) => String((e as Error)?.message || e)

const PROVIDERS: Record<string, string> = {
  telegram: 'Telegram',
  discord: 'Discord',
  slack: 'Slack',
  email: 'Email',
  'reference-echo': 'Reference (Echo)',
}

const providerLabel = (p: string) => PROVIDERS[p] ?? p

function viaLabel(via: string): string {
  if (via === 'owner') return 'You allowed them'
  if (via === 'pairing') return 'Redeemed a pairing code'
  if (!via) return 'Source unrecorded'
  return via
}

function dmPolicyLabel(policy: string): string {
  if (policy === 'pairing') return 'Strangers must redeem a pairing code'
  if (policy === 'owner_only') return 'Strangers are ignored silently'
  if (policy === 'open') return 'Anyone may talk to your agent'
  return policy
}

function groupPolicyLabel(policy: string): string {
  if (policy === 'tracked_only') return 'Only tracked groups are read'
  if (policy === 'off') return 'Group messages are ignored'
  return policy
}

function addedLabel(iso: string): string {
  if (!iso) return 'date unknown'
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return 'date unknown'
  return new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function SenderTrustPanel() {
  const { data, error: loadErr, refresh } = useQuery(CACHE_KEY, () => api.channelTrust(), { persist: true })
  const [revoking, setRevoking] = useState<string | null>(null)
  const [said, setSaid] = useState('')

  const revoke = async (provider: string, sender: ChannelTrustSender) => {
    const who = sender.name || sender.sender_id
    const where = providerLabel(provider)
    const ok = await confirm({
      title: `Revoke ${who}?`,
      body: `${who} will be dropped from your ${where} allowlist and their next message will be treated as a stranger's. Any turn already running is not interrupted. They can be let back in with a new pairing code.`,
      danger: true,
      confirmLabel: 'Revoke access',
    })
    if (!ok) return
    const tag = `${provider}:${sender.sender_id}`
    setRevoking(tag)
    try {
      await api.revokeChannelSender(provider, sender.sender_id)
      notify(`${who} can no longer talk to your agent on ${where}.`, 'success')
      setSaid(`${who} revoked on ${where}.`)
      refresh()
    } catch (e) {
      notify(`Couldn't revoke ${who}: ${msg(e)}`, 'error')
    } finally {
      setRevoking(null)
    }
  }

  if (!data && loadErr) return <LoadError what="sender trust" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={2} what="sender trust" />

  const providers = data.providers
  const total = providers.reduce((n, p) => n + p.allowed_senders.length, 0)

  return (
    <div className="space-y-2xl">
      <PanelHeader
        title="Sender trust"
        hint="Who is allowed to talk to your agent through a messaging channel — and the switch that cuts one off. Access is granted by a pairing code or by your Allow on an unknown-sender notification; this page is where you review and revoke it."
      />

      <ChannelOwnerSection />
      <ApprovalChannelSection />

      {providers.length === 0 ? (
        <EmptyState
          icon={MessageCircle}
          title="No channel has any trust state yet"
          hint="A channel appears here once someone messages it or you pair a sender. Run `gideon pair <channel>` to mint an 8-digit code."
        />
      ) : (
        providers.map((p) => <ProviderSection key={p.provider} p={p} revoking={revoking} onRevoke={revoke} refresh={refresh} />)
      )}

      {
}
      <div role="status" aria-live="polite" className="sr-only">{said}</div>
      <div className="sr-only">{total === 1 ? '1 trusted sender in total' : `${total} trusted senders in total`}</div>
    </div>
  )
}

function ProviderSection({ p, revoking, onRevoke, refresh }: {
  p: ChannelTrustProvider
  revoking: string | null
  onRevoke: (provider: string, sender: ChannelTrustSender) => void
  refresh: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [pairCode, setPairCode] = useState('')
  const label = providerLabel(p.provider)
  const senders = p.allowed_senders
  const seenChannels = p.seen_channels ?? []
  const groups = p.groups !== false || seenChannels.length > 0 || p.tracked_channels.length > 0
  const policySentence = p.speaks_as_owner && p.policies.dm !== 'open' ? 'Messages from strangers are held in your Inbox; nothing is sent to them.' : dmPolicyLabel(p.policies.dm) + '.'
  const updatePolicy = async (field: 'dm' | 'group', value: string) => {
    if (field === 'dm' && value === 'open') {
      const accepted = await confirm({ title: `Allow any ${label} user to message?`, body: p.speaks_as_owner ? 'Anyone who can reach this channel may start a conversation and receive replies as you. Confirm that you want your agent to answer as you.' : 'Anyone who can reach this channel may start a conversation with your agent. This is broader than pairing or owner-only access.', confirmLabel: 'Open direct messages' })
      if (!accepted) return
    }
    setBusy(true)
    try {
      await api.setChannelTrustPolicies(p.provider, { [field]: value, ...(field === 'dm' && value === 'open' ? { confirm_open: true } : {}) })
      refresh()
      notify(`${label} trust policy updated.`, 'success')
    } catch (error) { notify(`Couldn't update ${label} trust: ${msg(error)}`, 'error') }
    finally { setBusy(false) }
  }
  const pairing = async () => {
    setBusy(true)
    try {
      if (p.pairing_active) { await api.cancelChannelPairing(p.provider); setPairCode('') }
      else { const result = await api.createChannelPairing(p.provider); setPairCode(result.code) }
      refresh()
    } catch (error) { notify(`Couldn't update ${label} pairing: ${msg(error)}`, 'error') }
    finally { setBusy(false) }
  }
  return (
    <Section
      title={`${label}${senders.length ? ` (${senders.length})` : ''}`}
      icon={ShieldCheck}
      iconTone="muted"
      hint={`${policySentence}${groups ? ` ${groupPolicyLabel(p.policies.group)}.` : ''}`}
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1" data-type="caption"><span>Direct messages</span>
            <select aria-label={`${label} direct message trust`} disabled={busy} value={p.policies.dm} onChange={event => void updatePolicy('dm', event.currentTarget.value)} className="min-h-9 rounded-md border border-outline-low bg-surface px-2 text-on-surface">
              <option value="pairing">Pairing required</option><option value="owner_only">Owner only</option><option value="open">Anyone may message</option>
            </select>
          </label>
          {groups && <label className="flex flex-col gap-1" data-type="caption"><span>Groups</span>
            <select aria-label={`${label} group trust`} disabled={busy} value={p.policies.group} onChange={event => void updatePolicy('group', event.currentTarget.value)} className="min-h-9 rounded-md border border-outline-low bg-surface px-2 text-on-surface">
              <option value="tracked_only">Tracked groups only</option><option value="off">Ignore group messages</option>
            </select>
          </label>}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button size="xs" disabled={p.policies.dm !== 'pairing' && !p.pairing_active} onClick={() => void pairing()} loading={busy}>{p.pairing_active ? 'Cancel sender code' : 'Create sender code'}</Button>
          {pairCode ? <span role="status" className="select-all font-mono text-on-surface">{pairCode}</span> : null}
          {pairCode ? <span data-type="caption" className="text-on-surface-low">Shown once; expires in ten minutes.</span> : null}
        </div>
        {p.pairing_hint ? <p data-type="body-s" className="text-on-surface-low">{p.pairing_hint}</p> : null}
        {(p.seen_senders ?? []).length ? <div className="space-y-2"><p data-type="label-m">People who messaged your agent and aren’t paired</p><RowGroup>{p.seen_senders!.map((person, index) => {
          const who = channelPerson(label, person.sender_id, person.name)
          return <ListRow key={person.sender_id} index={index} label={who.name}><div className="py-2"><p data-type="body-s">{who.name}</p>{who.detail ? <p data-type="caption" className="text-on-surface-low">{who.detail}</p> : null}<p data-type="caption" className="text-on-surface-low">{person.count} {person.count === 1 ? 'message' : 'messages'} since {addedLabel(person.since)}; latest {addedLabel(person.last_seen)} · not read</p></div></ListRow>
        })}</RowGroup></div> : null}
        {seenChannels.length ? <div className="space-y-2"><p data-type="caption" className="text-on-surface-low">Groups seen on {label}</p>
          <RowGroup>{seenChannels.map((channel, i) => {
            const tracked = p.tracked_channels.some(item => item.channel_id === channel.channel_id)
            return <ListRow key={channel.channel_id} index={i} label={channel.name || channel.channel_id}>
              <Button size="xs" variant={tracked ? 'danger' : 'secondary'} loading={busy} onClick={async () => { setBusy(true); try { tracked ? await api.untrackChannel(p.provider, channel.channel_id) : await api.trackChannel(p.provider, channel.channel_id, channel.name); refresh() } catch (error) { notify(`Couldn't update tracked groups: ${msg(error)}`, 'error') } finally { setBusy(false) } }}>{tracked ? 'Untrack' : 'Track'}</Button>
            </ListRow>
          })}</RowGroup>
        </div> : null}
        {p.pairing_active && (
          <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low">
            <KeyRound size={16} className="shrink-0" aria-hidden="true" />
            <span>
              A pairing code is outstanding for {label}
              {p.pairing_expires_at ? ` until ${addedLabel(p.pairing_expires_at)}` : ''}. Anyone who
              sends it becomes a trusted sender.
            </span>
          </div>
        )}

        {senders.length === 0 ? (
          <EmptyState
            icon={UserCheck}
            title={`Nobody is trusted on ${label}`}
            hint={dmPolicyLabel(p.policies.dm) + '.'}
          />
        ) : (
          <RowGroup>
            {senders.map((s, i) => {
              const who = channelPerson(label, s.sender_id, s.name).name
              const tag = `${p.provider}:${s.sender_id}`
              return (
                <ListRow key={s.sender_id} index={i} label={who}>
                  <div className="flex items-start justify-between gap-l py-2">
                  <div className="flex min-w-0 items-start gap-3">
                    <UserCheck size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                    <div className="min-w-0">
                      <div data-type="body-s" className="truncate text-on-surface">{who}</div>
                      {
}
                      {s.name ? (
                        <div data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{s.sender_id}</div>
                      ) : null}
                      <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
                        {viaLabel(s.via)} · added {addedLabel(s.added_at)}
                      </div>
                    </div>
                  </div>
                  {
}
                  <Button
                    size="xs"
                    variant="danger"
                    onClick={() => onRevoke(p.provider, s)}
                    loading={revoking === tag}
                    ariaLabel={`Revoke ${who} on ${label}`}
                  >
                    Revoke
                  </Button>
                  </div>
                </ListRow>
              )
            })}
          </RowGroup>
        )}
      </div>
    </Section>
  )
}
