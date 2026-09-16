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

      {providers.length === 0 ? (
        <EmptyState
          icon={MessageCircle}
          title="No channel has any trust state yet"
          hint="A channel appears here once someone messages it or you pair a sender. Run `gideon pair <channel>` to mint an 8-digit code."
        />
      ) : (
        providers.map((p) => <ProviderSection key={p.provider} p={p} revoking={revoking} onRevoke={revoke} />)
      )}

      {
}
      <div role="status" aria-live="polite" className="sr-only">{said}</div>
      <div className="sr-only">{total === 1 ? '1 trusted sender in total' : `${total} trusted senders in total`}</div>
    </div>
  )
}

function ProviderSection({ p, revoking, onRevoke }: {
  p: ChannelTrustProvider
  revoking: string | null
  onRevoke: (provider: string, sender: ChannelTrustSender) => void
}) {
  const label = providerLabel(p.provider)
  const senders = p.allowed_senders
  return (
    <Section
      title={`${label}${senders.length ? ` (${senders.length})` : ''}`}
      icon={ShieldCheck}
      iconTone="muted"
      hint={`${dmPolicyLabel(p.policies.dm)}. ${groupPolicyLabel(p.policies.group)}.`}
    >
      <div className="space-y-3">
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
              const who = s.name || s.sender_id
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
