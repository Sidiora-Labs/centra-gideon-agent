import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, Ban, BellRing, LayoutDashboard, RefreshCw, ShieldCheck, CheckCheck, Smartphone } from 'lucide-react'
import { api, type PendingApproval, type PushStatus } from '../../shared/data/api'
import { disablePush, enablePush, pushDeviceId, pushSupported } from '../../app/shell/pushClient'
import { disableNativePush, enableNativePush, nativeBridge, watchNativePushTaps } from '../../app/shell/nativePush'
import { useQuery } from '../../shared/data/data'
import { useChatSocket } from '../../shared/data/useChatSocket'
import { ApprovalPrompt } from '../../shared/ui/ApprovalPrompt'
import { RungChip } from '../../shared/ui/RungChip'
import { providerRungIndex, useAutonomyLadder } from '../../shared/data/rungs'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { qget, type RouteProps } from '../../app/shell/useQueryState'
import { InboxSection, RecentSection, RunningLoopsSection, TasksSection } from './CompanionSections'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function CompanionPage({ navigate, query }: RouteProps) {
  const focusId = qget(query, 'approval')
  useEffect(() => {
    watchNativePushTaps((kind, itemId) => {
      if (kind === 'approval') navigate(`companion?approval=${encodeURIComponent(itemId)}`)
    })
  }, [navigate])
  const { data, loading, error, refresh } = useQuery<PendingApproval[]>(
    'companion:approvals', () => api.approvals())
  const [resolved, setResolved] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const busyRef = useRef(busy)
  busyRef.current = busy

  useEffect(() => {
    if (data === undefined) return
    setResolved((s) => {
      const next = new Set([...s].filter((id) => busyRef.current.has(id)))
      return next.size === s.size ? s : next
    })
  }, [data])

  const onWs = useCallback((m: { type: string }) => {
    if (m.type === 'approval' || m.type === 'approval_resolved') refresh()
  }, [refresh])
  useChatSocket(onWs)

  const act = async (ap: PendingApproval, action: 'approve' | 'reject') => {
    setBusy((s) => new Set(s).add(ap.id))
    setResolved((s) => new Set(s).add(ap.id))
    try {
      await api.resolveApproval(ap.id, action)
    } catch (e) {
      setResolved((s) => { const n = new Set(s); n.delete(ap.id); return n })
      window.dispatchEvent(new CustomEvent('ne:toast', {
        detail: { level: 'error', message: `Couldn't ${action} ${ap.tool} — ${(e as Error)?.message || 'the gateway did not respond'}` },
      }))
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(ap.id); return n })
      refresh()
    }
  }

  const pending = (data ?? []).filter((a) => !resolved.has(a.id))
  const { ladder } = useAutonomyLadder()
  const rungByProvider = useMemo(() => providerRungIndex(ladder), [ladder])
  const focusTarget = focusId ? pending.find((a) => a.id === focusId) : undefined
  const focusMissing = Boolean(focusId) && !focusTarget && data !== undefined
  const focusRef = useRef<HTMLDivElement | null>(null)
  const focusedFor = useRef('')
  useEffect(() => {
    if (!focusTarget || focusedFor.current === focusTarget.id) return
    const el = focusRef.current
    if (!el) return
    focusedFor.current = focusTarget.id
    el.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
    el.focus?.()
  }, [focusTarget])

  return (
    <div className="h-full overflow-y-auto" style={{ background: 'var(--color-canvas)' }}>
      <div className="mx-auto flex max-w-[42rem] flex-col gap-xl px-l py-l">
        <header className="flex items-center justify-between gap-s">
          <div className="min-w-0">
            <h1 data-type="headline-s" className="text-on-surface">Companion</h1>
            <p data-type="body-m" className="text-on-surface-low">Approve what your agent is waiting on.</p>
          </div>
          <IconButton icon={RefreshCw} label="Refresh approvals" onClick={refresh} size={40} iconSize={18} />
        </header>

        <PushRow navigate={navigate} />

        <section aria-labelledby="companion-approvals-heading" className="flex flex-col gap-s">
          <h2 id="companion-approvals-heading" data-type="title-m" className="flex items-center gap-s text-on-surface">
            <ShieldCheck size={16} aria-hidden style={{ color: 'var(--color-warn)' }} />
            Approvals{pending.length > 0 ? ` (${pending.length})` : ''}
          </h2>
          {
}
          {data === undefined && error ? (
            <LoadError what="approvals" error={error} onRetry={refresh} />
          ) : data === undefined && loading ? (
            <ListSkeleton rows={2} what="approvals" />
          ) : pending.length === 0 ? (
            <EmptyState icon={CheckCheck} title="Nothing waiting on you"
              hint="Tool approvals your agent raises will appear here." />
          ) : (
            <AnimatePresence initial={false}>
              {pending.map((ap) => (
                <div
                  key={ap.id}
                  ref={ap.id === focusId ? focusRef : undefined}
                  tabIndex={ap.id === focusId ? -1 : undefined}
                  className={ap.id === focusId ? 'rounded-lg outline-none ring-2 ring-warn' : undefined}
                >
                <ApprovalPrompt
                  density="roomy"
                  tool={ap.tool}
                  args={argsText(ap.tool_input)}
                  purpose={ap.tool_purpose}
                  badge={
                    rungByProvider.get(ap.tool) ? (
                      <RungChip type={rungByProvider.get(ap.tool)!} ladder={ladder} />
                    ) : undefined
                  }
                  meta={<ApprovalMeta ap={ap} />}
                  choices={[
                    { key: 'approve', icon: Check, label: 'Allow', tone: 'primary', name: `Allow ${ap.tool}`, busy: busy.has(ap.id), onClick: () => act(ap, 'approve') },
                    { key: 'reject', icon: Ban, label: 'Deny', tone: 'danger', name: `Deny ${ap.tool}`, busy: busy.has(ap.id), onClick: () => act(ap, 'reject') },
                  ]}
                />
                </div>
              ))}
            </AnimatePresence>
          )}
          {focusMissing ? (
            <p role="status" data-type="body-m" className="text-on-surface-low">
              The approval your notification pointed at isn&rsquo;t waiting anymore &mdash; it was
              answered or it timed out.
            </p>
          ) : null}
        </section>

        {
}
        <RunningLoopsSection />
        <TasksSection />
        <InboxSection />
        <RecentSection />

        {
}
        <footer className="flex flex-wrap gap-s">
          <Button variant="secondary" size="sm" onClick={() => navigate('dashboard')}>
            <LayoutDashboard size={15} /> Open the full dashboard
          </Button>
          <Button variant="secondary" size="sm" onClick={() => navigate('settings/devices')}>
            <Smartphone size={15} /> Paired devices
          </Button>
        </footer>
      </div>
    </div>
  )
}

function PushRow({ navigate }: { navigate: RouteProps['navigate'] }) {
  const { data, refresh } = useQuery<PushStatus>('companion:push', () => api.pushStatus())
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  if (!data) return null

  const supported = pushSupported()
  const on = data.devices.includes(pushDeviceId())

  const enable = async () => {
    setBusy(true)
    setNote('')
    const result = await enablePush(data.vapid_public_key)
    setBusy(false)
    if (result.ok) { refresh(); return }
    setNote(
      result.reason === 'denied'
        ? 'Your browser refused notification permission. Allow notifications for this site, then try again.'
        : result.reason === 'no-key'
          ? 'This gateway has no push keypair yet — run `gideon push init`.'
          : result.reason === 'unsupported'
            ? 'This browser cannot hold a push subscription.'
            : `Could not subscribe${result.detail ? ` — ${result.detail}` : ''}.`,
    )
  }

  const disable = async () => {
    setBusy(true)
    const ok = await disablePush()
    setBusy(false)
    setNote(ok ? '' : 'Could not unsubscribe — the gateway did not respond.')
    refresh()
  }

  let line = ''
  let action: ReactNode = null
  if (data.backend === 'none') {
    line = 'Push is switched off for this gateway.'
    action = <Button variant="secondary" size="sm" onClick={() => navigate('settings/companion')}>Settings</Button>
  } else if (data.backend === 'ntfy') {
    line = !data.ntfy_configured
      ? 'The backend is ntfy but no topic URL is configured.'
      : data.approval_targeted
        ? 'Pings go to your ntfy topic. Nothing to set up on this device.'
        : 'Pings go to your ntfy topic, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
  } else if (data.backend === 'relay') {
    const registered = data.relay_devices.includes(pushDeviceId())
    if (!data.relay_configured) {
      line = 'The backend is relay but no relay URL is configured.'
      action = <Button variant="secondary" size="sm" onClick={() => navigate('settings/companion')}>Settings</Button>
    } else if (registered) {
      line = data.approval_targeted
        ? 'Push is on for this device.'
        : 'Push is on for this device, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
      action = (
        <Button variant="ghost" size="sm" disabled={busy} disabledReason={BUSY_REASON}
          onClick={async () => {
            setBusy(true)
            const ok = await disableNativePush()
            setBusy(false)
            setNote(ok ? '' : 'Could not unregister — the gateway did not respond.')
            refresh()
          }}>Turn off</Button>
      )
    } else if (!nativeBridge()) {
      line = 'Pings go through the push relay to the store app. Open this page in the Gideon app to register this device.'
    } else {
      line = 'Get woken up when a run needs your approval.'
      action = (
        <Button variant="primary" size="sm" disabled={busy} disabledReason={BUSY_REASON}
          onClick={async () => {
            setBusy(true)
            setNote('')
            const result = await enableNativePush()
            setBusy(false)
            if (result.ok) { refresh(); return }
            setNote(
              result.reason === 'denied'
                ? 'The OS refused notification permission. Allow notifications for Gideon, then try again.'
                : `Could not register${result.detail ? ` — ${result.detail}` : ''}.`,
            )
          }}><BellRing size={15} /> Turn on push</Button>
      )
    }
  } else if (!data.vapid_ready) {
    line = 'No push keypair yet. Run `gideon push init` on the gateway.'
  } else if (on) {
    line = data.approval_targeted
      ? 'Push is on for this device.'
      : 'Push is on for this device, but approvals are not routed to it. Tick “push” for “Approval needed” in Settings → Notifications.'
    action = <Button variant="ghost" size="sm" onClick={disable} disabled={busy} disabledReason={BUSY_REASON}>Turn off</Button>
  } else if (!supported) {
    line = 'This browser cannot hold a push subscription. Install to your home screen first.'
  } else {
    line = 'Get woken up when a run needs your approval.'
    action = <Button variant="primary" size="sm" onClick={enable} disabled={busy} disabledReason={BUSY_REASON}><BellRing size={15} /> Turn on push</Button>
  }

  return (
    <div className="flex flex-col gap-s rounded-lg border border-outline-variant/40 px-l py-l">
      <div className="flex items-center justify-between gap-m">
        <p data-type="body-m" className="min-w-0 text-on-surface-low">{line}</p>
        {action}
      </div>
      {note ? <p role="alert" data-type="body-m" className="text-warn">{note}</p> : null}
    </div>
  )
}

function ApprovalMeta({ ap }: { ap: PendingApproval }) {
  const rows: [string, string][] = []
  if (ap.session) rows.push(['Session', ap.session])
  if (ap.source) rows.push(['Requested by', ap.source])
  const waited = waitedFor(ap.ts)
  if (waited) rows.push(['Waiting', waited])
  if (!rows.length) return null
  return (
    <dl className="mt-s flex flex-col gap-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="flex gap-s text-[0.75rem]">
          <dt className="shrink-0 text-on-surface-low">{k}</dt>
          <dd className="min-w-0 break-all text-on-surface-var">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function argsText(input: unknown): string | undefined {
  if (input == null || input === '') return undefined
  if (typeof input === 'string') return input
  try { return JSON.stringify(input, null, 2) } catch { return String(input) }
}

function waitedFor(ts: number | undefined): string {
  if (!ts) return ''
  const secs = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (secs < 60) return `${secs}s`
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m`
  return `${Math.round(mins / 60)}h`
}
