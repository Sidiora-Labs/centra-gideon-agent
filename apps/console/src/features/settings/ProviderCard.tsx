import { useState } from 'react'
import { ChevronDown, KeyRound, AlertTriangle, CheckCircle2, Clock, TerminalSquare, RefreshCw, Beaker, Plug, PlugZap, Loader2 } from 'lucide-react'
import { api, type SettingsProvider, type AgentRuntime, type ChannelRuntime } from '../../shared/data/api'
import { Toggle } from './settingsUI'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { ProviderConfigForm } from './ProviderConfigForm'
import { fvs } from '../../shared/theme/fontWeight'

export function ProviderCard({ ext, runtime, channel, open, onOpenChange, onChanged, onSignIn, onRecheck, onChannelChanged }: {
  ext: SettingsProvider; runtime?: AgentRuntime; channel?: ChannelRuntime; open: boolean; onOpenChange: (v: boolean) => void; onChanged: () => void
  onSignIn?: (rt: AgentRuntime) => void; onRecheck?: () => Promise<void> | void; onChannelChanged?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [rechecking, setRechecking] = useState(false)
  const hasConfig = !!ext.enabled && ext.provider?.hasConfigSchema === true
  const unavailable = ext.available === false
  const who = ext.displayName || ext.name
  const managed = ext.managed === true

  const toggle = async () => {
    setBusy(true)
    try { ext.enabled ? await api.disableProvider(ext.name) : await api.enableProvider(ext.name); onChanged() }
    finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: unavailable ? 0.6 : busy ? 0.6 : 1 }}>
      <div className="flex items-center gap-3">
        { }
        <span className="size-2 shrink-0 rounded-full" style={{ background: ext.enabled && !unavailable ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{ext.displayName || ext.name}</span>
            {ext.version && <span data-type="caption" className="text-on-surface-low">v{ext.version}</span>}
            {(ext.provider?.capabilities ?? []).map((c) => (
              <span key={c} data-type="caption" className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface-low">{c}</span>
            ))}
            {unavailable && <span data-type="caption" className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low"><AlertTriangle size={10} /> unavailable</span>}
          </div>
          {ext.description && <p data-type="body-s" className="mt-0.5 truncate text-on-surface-low">{ext.description}</p>}
        </div>

        {runtime && !unavailable && <RuntimeChip state={runtime.state} />}
        {
}
        {runtime && runtime.state === 'needs_login' && runtime.login_command && onSignIn && (
          <button type="button" onClick={() => onSignIn(runtime)} aria-label={`Sign in: ${who}`}
            data-type="caption" className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2.5 py-1 text-on-surface hover:bg-surface-highest">
            <KeyRound size={12} /> Sign in
          </button>
        )}
        {
}
        {runtime && runtime.type !== 'native' && !unavailable && onRecheck && (
          <SquareIconButton label={`Check availability: ${who}`} title="Check availability" loading={rechecking} className="shrink-0"
            onClick={async () => { setRechecking(true); try { await onRecheck() } finally { setRechecking(false) } }}>
            <RefreshCw size={14} />
          </SquareIconButton>
        )}
        {
}
        {!unavailable && (managed
          ? <Toggle on={ext.enabled} onChange={toggle} label={`Toggle ${ext.name}`} />
          : <span data-type="caption" className="shrink-0 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low" title="Built-in provider — always available">Always on</span>
        )}
        {
}
        {hasConfig && (
          <SquareIconButton label={`Configure: ${who}`} title="Configure" ariaExpanded={open}
            onClick={() => onOpenChange(!open)} className="shrink-0">
            <ChevronDown size={16} className="transition-transform" style={{ transform: open ? 'rotate(180deg)' : 'none' }} />
          </SquareIconButton>
        )}
      </div>

      { }
      {unavailable && ext.unavailableReason && (
        <div data-type="caption" className="mt-2 flex items-start gap-1.5 text-on-surface-low"><AlertTriangle size={12} className="mt-0.5 shrink-0" /> {ext.unavailableReason}</div>
      )}
      {runtime && runtime.detail && runtime.state !== 'ready' && !unavailable && (
        <div data-type="caption" className="mt-2 flex items-start gap-1.5 text-on-surface-low"><TerminalSquare size={12} className="mt-0.5 shrink-0" /> {runtime.detail}</div>
      )}
      {ext.error && <div data-type="caption" className="mt-2 flex items-center gap-1.5" style={{ color: 'var(--color-danger)' }}><AlertTriangle size={12} /> {ext.error}</div>}

      {
}
      {channel && <ChannelRuntimeRow channel={channel} onChanged={onChannelChanged} />}

      {open && hasConfig && <ProviderConfigForm name={ext.name} />}
    </div>
  )
}

const CHANNEL_STATE_TONE: Record<string, string> = {
  connected: 'var(--color-ok)', ready: 'var(--color-ok)', online: 'var(--color-ok)',
  error: 'var(--color-danger)', offline: 'var(--color-on-surface-low)',
}

function ChannelRuntimeRow({ channel, onChanged }: { channel: ChannelRuntime; onChanged?: () => void }) {
  const [busy, setBusy] = useState('')
  const [detail, setDetail] = useState<string | null>(null)
  const tone = CHANNEL_STATE_TONE[channel.health.state] ?? 'var(--color-on-surface-low)'
  const act = async (kind: 'test' | 'connect' | 'disconnect') => {
    if (busy) return
    setBusy(kind); setDetail(null)
    try {
      const r = kind === 'test' ? await api.testChannel(channel.name)
        : kind === 'connect' ? await api.connectChannel(channel.name)
        : await api.disconnectChannel(channel.name)
      const d = (r as { health?: { detail?: string }; detail?: string })
      setDetail(d.detail ?? d.health?.detail ?? (kind === 'test' ? 'Tested' : kind === 'connect' ? 'Connected' : 'Disconnected'))
      onChanged?.()
    } catch (e) { setDetail(e instanceof Error ? e.message : 'Failed') }
    finally { setBusy('') }
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-outline-variant/30 pt-2">
      <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-var">
        <span className="size-2 rounded-full" style={{ background: tone }} />
        {channel.connected ? 'Connected' : channel.health.state === 'error' ? 'Error' : 'Not connected'}
      </span>
      {(detail ?? channel.health.detail) && <span data-type="caption" className="text-on-surface-low truncate max-w-[60%]">{detail ?? channel.health.detail}</span>}
      <div className="ml-auto flex items-center gap-1.5">
        { }
        <button type="button" onClick={() => act('test')} disabled={!!busy} aria-label={`Test: ${channel.name}`}
          data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-on-surface disabled:opacity-50">
          {busy === 'test' ? <Loader2 size={11} className="animate-spin" /> : <Beaker size={11} />} Test
        </button>
        {channel.connected
          ? <button type="button" onClick={() => act('disconnect')} disabled={!!busy} aria-label={`Disconnect: ${channel.name}`}
              data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-danger disabled:opacity-50">
              {busy === 'disconnect' ? <Loader2 size={11} className="animate-spin" /> : <Plug size={11} />} Disconnect
            </button>
          : <button type="button" onClick={() => act('connect')} disabled={!!busy} aria-label={`Connect: ${channel.name}`}
              data-type="caption" className="inline-flex items-center gap-1 rounded-md bg-surface-high px-2 py-1 text-on-surface-var hover:text-primary disabled:opacity-50">
              {busy === 'connect' ? <Loader2 size={11} className="animate-spin" /> : <PlugZap size={11} />} Connect
            </button>}
      </div>
    </div>
  )
}

function RuntimeChip({ state }: { state: string }) {
  const map: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
    ready: { icon: <CheckCircle2 size={12} />, label: 'Ready', color: 'var(--color-success)' },
    needs_login: { icon: <KeyRound size={12} />, label: 'Needs sign-in', color: 'var(--color-warning)' },
    not_found: { icon: <AlertTriangle size={12} />, label: 'Not found', color: 'var(--color-on-surface-low)' },
    timeout: { icon: <Clock size={12} />, label: 'Slow to start', color: 'var(--color-warning)' },
    error: { icon: <AlertTriangle size={12} />, label: 'Error', color: 'var(--color-danger)' },
  }
  const m = map[state] ?? map.error
  return <span data-type="caption" className="inline-flex shrink-0 items-center gap-1" style={{ color: m.color }}>{m.icon} {m.label}</span>
}
