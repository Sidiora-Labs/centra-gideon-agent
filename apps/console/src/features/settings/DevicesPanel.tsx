import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Check, Copy, Laptop, MonitorSmartphone, QrCode, Smartphone, Terminal, Globe, XCircle,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../../shared/data/api'
import type { DeviceRec, DevicePairStart } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { confirm } from '../../shared/ui/dialog'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup } from './settingsUI'
import { PairingQr } from './PairingQr'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { relPast, absTime } from '../schedule/scheduleMeta'
import { copyText } from '../../app/shell/clipboard'

const msg = (e: unknown) => String((e as Error)?.message || e)

const KINDS: Record<DeviceRec['kind'], { label: string; icon: LucideIcon }> = {
  browser: { label: 'Browser', icon: Globe },
  mobile: { label: 'Phone', icon: Smartphone },
  desktop: { label: 'Desktop', icon: Laptop },
  cli: { label: 'Terminal', icon: Terminal },
  unknown: { label: 'Unknown', icon: MonitorSmartphone },
}

function issuerLabel(issuer: string): string {
  if (issuer === 'pair') return 'Paired with a code'
  if (issuer === 'unknown') return 'Unknown'
  return issuer
}

function secsLeft(expiresAt: number): number {
  return Math.max(0, Math.floor(expiresAt - Date.now() / 1000))
}

function mmss(total: number): string {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    if (!(await copyText(value, `the ${label}`))) return
    setDone(true)
    setTimeout(() => setDone(false), 1500)
  }
  return (
    <Button size="xs" variant="secondary" onClick={copy} ariaLabel={done ? `${label} copied` : `Copy ${label}`}>
      {done ? <Check size={14} /> : <Copy size={14} />} {done ? 'Copied' : 'Copy'}
    </Button>
  )
}

export function DevicesPanel() {
  const { data, error: loadErr, refresh } = useQuery('settings:devices', () => api.devices())
  const [pairing, setPairing] = useState<DevicePairStart | null>(null)
  const [starting, setStarting] = useState(false)
  const [left, setLeft] = useState(0)
  const announce = useRef('')
  const [said, setSaid] = useState('')
  const codeRef = useRef<HTMLDivElement | null>(null)
  const [revoking, setRevoking] = useState<string | null>(null)

  useEffect(() => {
    if (!pairing) return
    setLeft(secsLeft(pairing.expires_at))
    const t = setInterval(() => setLeft(secsLeft(pairing.expires_at)), 1000)
    return () => clearInterval(t)
  }, [pairing])

  const startPairing = useCallback(() => {
    setStarting(true)
    api.devicePairStart()
      .then((p) => { setLeft(secsLeft(p.expires_at)); setPairing(p) })
      .catch((e) => notify(`Couldn't start pairing: ${msg(e)}`, 'error'))
      .finally(() => setStarting(false))
  }, [])

  const revoke = async (device: DeviceRec) => {
    const name = device.name || 'this device'
    const ok = await confirm({
      title: `Revoke ${name}?`,
      body: `${name} will lose access to this gateway immediately and will have to pair again with a new code.`,
      danger: true,
      confirmLabel: 'Revoke access',
    })
    if (!ok) return
    setRevoking(device.id)
    try {
      await api.deviceRevoke(device.id)
      notify(`${name} can no longer reach this gateway.`, 'success')
      refresh()
    } catch (e) {
      notify(`Couldn't revoke ${name}: ${msg(e)}`, 'error')
    } finally {
      setRevoking(null)
    }
  }

  const expired = pairing != null && left <= 0

  useEffect(() => {
    if (!pairing) { announce.current = ''; setSaid(''); return }
    if (announce.current === pairing.code) return
    announce.current = pairing.code
    const mins = Math.max(1, Math.round((pairing.expires_in ?? 300) / 60))
    setSaid(`Pairing code ${pairing.code} is ready. It expires in about ${mins} minute${mins === 1 ? '' : 's'}.`)
    codeRef.current?.focus()
  }, [pairing])

  useEffect(() => {
    if (expired) setSaid('This pairing code has expired. Generate another.')
  }, [expired])

  if (!data && loadErr) return <LoadError what="devices" error={loadErr} onRetry={refresh} />
  if (!data) return <FormSkeleton sections={2} what="devices" />

  return (
    <div>
      <PanelHeader
        title="Devices"
        hint="Phones, tablets and other browsers you have paired with this gateway. Each one holds an ordinary session, so revoking a device logs exactly that device out."
      />

      <Section
        title="Pair a device"
        hint="Open the link on the other device — on the same network — and it joins with the code below. The code is single-use and short-lived."
      >
        <div className="rounded-lg bg-surface-container px-4 py-4">
          {!pairing ? (
            <div className="flex flex-wrap items-center justify-between gap-l">
              <p data-type="body-s" className="min-w-0 flex-1 text-on-surface-low">
                Generates a one-time code and a link for the device to open.
              </p>
              <Button size="sm" onClick={startPairing} loading={starting} ariaLabel="Pair a device">
                <QrCode size={16} /> Pair a device
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-l">
              {
}
              <div className="flex flex-wrap items-start gap-l">
                <PairingQr url={pairing.pairing_url} expired={expired} />

                {
}
                {expired ? (
                  <p data-type="body-s" className="min-w-0 flex-1 text-on-surface-low">
                    The code and link are no longer shown — this gateway refuses an expired code,
                    so there is nothing here that would still work.
                  </p>
                ) : (
                  <div ref={codeRef} tabIndex={-1} role="group" aria-label="Pairing code and link"
                    className="min-w-0 flex-1 flex flex-col gap-l outline-none">
                    <div>
                      <div data-type="body-s" className="text-on-surface-low">Code</div>
                      <div className="mt-1 flex items-center gap-s">
                        {
}
                        <code className="select-all font-mono text-on-surface text-[1.375rem] tracking-[0.12em]">
                          {pairing.code}
                        </code>
                        <CopyButton value={pairing.code} label="pairing code" />
                      </div>
                    </div>

                    <div>
                      <div data-type="body-s" className="text-on-surface-low">Link to open on the device</div>
                      <div className="mt-1 flex items-start gap-s">
                        <code data-type="body-s" className="min-w-0 select-all break-all font-mono text-on-surface">
                          {pairing.pairing_url}
                        </code>
                        <CopyButton value={pairing.pairing_url} label="pairing link" />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {
}
              <div className="flex flex-wrap items-center justify-between gap-l border-t border-outline-variant/30 pt-3">
                {
}
                <span
                  data-type="body-s" className={`inline-flex items-center gap-1.5 ${expired ? 'text-warn' : 'text-on-surface-low'}`}
                >
                  {expired ? <XCircle size={14} /> : null}
                  {expired ? 'This code has expired — generate another.' : `Expires in ${mmss(left)}`}
                </span>
                <div className="flex items-center gap-s">
                  <Button size="xs" variant="secondary" onClick={startPairing} loading={starting}
                    ariaLabel="Generate a new pairing code">
                    New code
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => { setPairing(null); refresh() }}
                    ariaLabel="Done pairing">
                    Done
                  </Button>
                </div>
              </div>
            </div>
          )}
          {
}
          <div role="status" aria-live="polite" className="sr-only">{said}</div>
        </div>
      </Section>

      <Section title={`Paired devices${data.length ? ` (${data.length})` : ''}`}
        hint="Revoking a device drops its session on this gateway and on disk, so it stays locked out across a restart.">
        {data.length === 0 ? (
          <EmptyState
            icon={MonitorSmartphone}
            title="No devices paired"
            hint="Nothing but this browser can reach your gateway with a paired session."
            action={{ label: 'Pair your first device', onClick: startPairing, icon: QrCode }}
          />
        ) : (
          <RowGroup>
            {data.map((d) => {
              const kind = KINDS[d.kind] ?? KINDS.unknown
              const KindIcon = kind.icon
              const name = d.name || 'Unnamed device'
              return (
                <div key={d.id}
                  className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
                  <div className="flex min-w-0 items-start gap-3">
                    <KindIcon size={18} className="mt-0.5 shrink-0 text-on-surface-low" aria-hidden="true" />
                    <div className="min-w-0">
                      <div data-type="body-s" className="truncate text-on-surface">{name}</div>
                      {
}
                      <div data-type="body-s" className="mt-0.5 text-on-surface-low">
                        {kind.label}
                        {' · '}
                        <span>Last seen {d.last_seen > 0 ? relPast(d.last_seen) : 'never'}</span>
                        {' · '}
                        <span>{issuerLabel(d.issuer)}</span>
                      </div>
                      <div data-type="caption" className="mt-0.5 text-on-surface-low/80">
                        Paired {d.minted_at > 0 ? relPast(d.minted_at) : 'unknown'}
                        {d.expires_at > 0 ? ` · session expires ${absTime(d.expires_at)}` : ''}
                      </div>
                    </div>
                  </div>
                  <Button size="xs" variant="danger" onClick={() => revoke(d)}
                    loading={revoking === d.id} ariaLabel={`Revoke ${name}`}>
                    Revoke
                  </Button>
                </div>
              )
            })}
          </RowGroup>
        )}
      </Section>
    </div>
  )
}
