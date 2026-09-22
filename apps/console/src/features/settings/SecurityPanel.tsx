import { useState } from 'react'
import { FieldError } from '../../shared/ui/forms'
import { unavailableWhen, BUSY_REASON } from '../../shared/ui/unavailable'
import {
  ShieldBan, ScanLine, FileCode2, EyeOff, Plus, X, Lock, Globe, MonitorOff, ShieldCheck, ShieldAlert,
  KeyRound, Undo2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import {
  api, type DesktopCapabilityWire, type EgressPolicyConfig, type DenylistBaseline,
} from '../../shared/data/api'
import { confirm } from '../../shared/ui/dialog'
import {
  desktopBridge, getLoginItem, requestDesktopCapability, setLoginItem,
} from '../../shared/data/desktopBridge'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, SavedToast } from './settingsUI'
import { CardGridSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'

export function SecurityPanel() {
  const { data: s, error: loadErr, refresh: refreshStats } = useQuery(
    'settings:security', () => api.securityStats(), { persist: true },
  )
  const { data: denied, error: deniedErr, refresh: refreshDenied } = useQuery(
    'settings:denied-commands', () => api.deniedCommands(), { persist: true },
  )
  const onDeniedChange = () => { refreshDenied(); refreshStats() }
  if (!s && loadErr) return <LoadError what="security settings" error={loadErr} onRetry={refreshStats} />
  if (!s) return <CardGridSkeleton cards={4} cols={2} what="security settings" />

  const cards: { icon: LucideIcon; label: string; value: number; hint: string }[] = [
    { icon: ShieldBan, label: 'Denied commands', value: s.denied_commands, hint: 'Shell patterns blocked from execution' },
    { icon: ScanLine, label: 'Suspicious patterns', value: s.suspicious_patterns, hint: 'Prompt-injection / exfiltration signatures watched' },
    { icon: FileCode2, label: 'Tool schemas', value: s.tool_schemas, hint: 'Tools with enforced argument validation' },
    { icon: EyeOff, label: 'Redaction paths', value: s.redaction_paths, hint: 'Sensitive paths redacted from output' },
  ]

  return (
    <div>
      <PanelHeader title="Security" hint="The enforcement posture protecting this self-hosted instance. Built-in protections are managed in code; you can extend the shell denylist below." />
      <Section title="Active protections">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cards.map((c) => (
            <div key={c.label} className="flex items-start gap-3 rounded-lg bg-surface-container px-4 py-3">
              <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
                <c.icon size={17} className="text-primary" />
              </span>
              <div className="min-w-0">
                <div className="text-on-surface text-[1.25rem] tabular-nums" style={fvs(600)}>{c.value}</div>
                <div data-type="body-s" className="text-on-surface">{c.label}</div>
                <div data-type="body-s" className="mt-0.5 text-on-surface-low">{c.hint}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>
      {!denied && deniedErr ? (
        <Section title="Shell denylist">
          <LoadError what="shell denylist patterns" error={deniedErr} onRetry={refreshDenied} />
        </Section>
      ) : denied ? (
        <DeniedCommandsEditor builtin={denied.builtin} user={denied.user}
          baseline={denied.baseline} userAdditions={denied.user_additions}
          onChange={onDeniedChange} />
      ) : null}
      <CredentialStoreEditor />
      <EgressPolicyEditor />
      <DesktopCapabilitiesPanel />
    </div>
  )
}

function CredentialStoreEditor() {
  const { data: cs, error, refresh } = useQuery(
    'settings:credential-store', () => api.credentialStore(),
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  if (!cs) {
    return (
      <Section title="Credential storage">
        {error ? <LoadError what="credential storage state" error={error} onRetry={refresh} />
          : <CardGridSkeleton cards={1} cols={1} what="credential storage state" />}
      </Section>
    )
  }

  const run = async (label: string, fn: () => Promise<{ reason: string; moved: string[]; failed: string[] }>) => {
    setBusy(true); setErr(''); setNote('')
    try {
      const r = await fn()
      if (r.reason) setErr(r.reason)
      setNote(`${label}: ${r.moved.length} credential${r.moved.length === 1 ? '' : 's'}.`)
      refresh()
    } catch (e) { setErr(e instanceof Error ? e.message : `${label} failed`) }
    finally { setBusy(false) }
  }

  const move = async () => {
    const names = cs.pending_keys.join(', ')
    if (!(await confirm({
      title: `Move ${cs.pending} credential${cs.pending === 1 ? '' : 's'} into the OS keychain?`,
      body: `Your current .env is copied to ${cs.snapshot_name} first (mode 0600), then ${names} `
        + 'move into the keychain and are removed from .env. No key leaves .env until its value has '
        + 'been read back out of the keychain. Use Roll back to undo this and restore .env exactly.',
      confirmLabel: 'Snapshot and move',
      danger: true,
    }))) return
    await run('Moved', () => api.migrateCredentialsToKeychain())
  }

  const rollBack = async () => {
    if (!(await confirm({
      title: 'Restore .env from the pre-migration snapshot?',
      body: `.env is rewritten from ${cs.snapshot_name} byte for byte, the keychain copies of those `
        + 'keys are deleted, and the snapshot file is removed. Credentials you added to the keychain '
        + 'after migrating are left alone.',
      confirmLabel: 'Roll back',
      danger: true,
    }))) return
    await run('Restored', () => api.rollbackCredentialsToKeychain())
  }

  const inKeychain = cs.backend === 'keychain'
  return (
    <Section title="Credential storage" hint="Where this instance keeps provider credentials. The default is ~/.gideon/.env at mode 0600; the OS keychain (macOS Keychain, Linux Secret Service, Windows Credential Locker) is an opt-in upgrade. A machine with no usable secret service keeps using .env and says so — there is never a third location.">
      <div className="flex flex-col gap-4">
        <div className="flex items-start gap-3 rounded-lg bg-surface-container px-4 py-3">
          <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
            {inKeychain ? <KeyRound size={17} className="text-primary" /> : <Lock size={17} className="text-primary" />}
          </span>
          <div className="min-w-0">
            <div data-type="label-s" className="text-on-surface" style={fvs(600)}>
              {inKeychain ? 'OS keychain' : '.env at mode 0600'}
            </div>
            <div data-type="body-s" className="mt-0.5 text-on-surface-low">
              {cs.keychain_keys} in the keychain · {cs.pending} still in .env
            </div>
            {cs.blocked && cs.requested === 'keychain' && (
              <div data-type="body-s" className="mt-1 text-error">
                The keychain was requested but no usable OS keyring backend answered on this
                machine. Credentials stay in .env at mode 0600.
              </div>
            )}
          </div>
        </div>
        <div className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5">
          <Toggle on={cs.requested === 'keychain'} disabled={busy}
            label="Store credentials in the OS keychain"
            onChange={async (on) => {
              setBusy(true); setErr(''); setNote('')
              try { await api.setCredentialKeychain(on); refresh() }
              catch (ex) { setErr(ex instanceof Error ? ex.message : 'Failed to save') }
              finally { setBusy(false) }
            }} />
          <span className="min-w-0">
            <span data-type="body-s" className="text-on-surface">Store credentials in the OS keychain</span>
            <span data-type="body-s" className="block text-on-surface-low">Changes where NEW credentials are written. Secrets already in .env stay readable and stay put until you move them below.</span>
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {
}
          <Button onClick={move} disabled={busy || cs.blocked || cs.pending === 0}
            disabledReason={cs.blocked
              ? 'Turn on "Store credentials in the OS keychain" first — and this machine needs a working OS secret service'
              : cs.pending === 0 ? 'There are no credentials left in .env to move' : undefined}>
            <KeyRound size={15} /> Move {cs.pending > 0 ? cs.pending : ''} to keychain
          </Button>
          {cs.rollback_available && (
            <Button variant="secondary" onClick={rollBack} disabled={busy} disabledReason={BUSY_REASON}>
              <Undo2 size={15} /> Roll back
            </Button>
          )}
          {cs.pending === 0 && !cs.blocked && (
            <span data-type="body-s" className="text-on-surface-low">
              Nothing left in .env{cs.verification.checked > 0 ? ` — ${cs.verification.checked} verified in the keychain` : ''}.
            </span>
          )}
        </div>
        {note && <div data-type="body-s" className="text-on-surface-low" role="status">{note}</div>}
        {err && <FieldError>{err}</FieldError>}
      </div>
    </Section>
  )
}

const DESKTOP_CAPABILITY_LABELS: Record<string, string> = {
  audio_capture: 'Microphone',
  screen_capture: 'Screen recording',
  native_notifications: 'Native notifications',
  global_hotkey: 'Global hotkey',
  tray: 'Menu-bar item',
  login_item: 'Open at login',
}

const capabilityLabel = (cap: string) =>
  DESKTOP_CAPABILITY_LABELS[cap] ?? cap.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

function LoginItemRow({ label }: { label: string }) {
  const { data: state, refresh } = useQuery('settings:login-item', () => getLoginItem())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState(false)

  if (!state) return null

  const flip = async (next: boolean) => {
    setBusy(true)
    setErr('')
    const res = await setLoginItem(next)
    if (!res) setErr('The desktop app is no longer connected.')
    else if (!res.ok) setErr(res.reason || 'macOS did not apply the change.')
    else { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }
    setBusy(false)
    refresh()
  }

  return (
    <div className="flex items-start justify-between gap-3 rounded-lg bg-surface-container px-4 py-3">
      <div className="min-w-0">
        <div data-type="label-s" className="text-on-surface" style={fvs(600)}>{label}</div>
        <div data-type="body-s" className={`mt-0.5 ${state.enabled ? 'text-success' : 'text-on-surface-low'}`}>
          {state.enabled
            ? 'On — Gideon starts when you log in'
            : 'Off — Gideon starts only when you open it'}
        </div>
        {
}
        <div data-type="body-s" className="mt-0.5 text-on-surface-low">{state.describes}</div>
        {err && <FieldError>{err}</FieldError>}
      </div>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {
}
        <Toggle on={state.enabled} onChange={flip} label={label}
          disabled={!state.supported || busy}
          disabledReason={state.supported ? undefined : state.describes} />
      </div>
    </div>
  )
}

const GRANT_PRESENTATION: Record<DesktopCapabilityWire['granted'], { label: string; tone: string }> = {
  granted: { label: 'Granted', tone: 'text-success' },
  denied: { label: 'Denied', tone: 'text-error' },
  restricted: { label: 'Restricted by policy', tone: 'text-error' },
  'not-determined': { label: 'Not requested yet', tone: 'text-on-surface-low' },
  unavailable: { label: 'Unavailable', tone: 'text-on-surface-low' },
}

function DesktopCapabilitiesPanel() {
  const { data: ds, refresh } = useQuery(
    'settings:desktop-state', () => api.desktopState().catch(() => null),
  )
  const [busyCap, setBusyCap] = useState('')
  const [err, setErr] = useState('')
  const loginItemBridge = !!desktopBridge()?.loginItem
  if (!ds) return null

  const caps = Object.entries(ds.capabilities)

  const request = async (cap: string) => {
    setBusyCap(cap); setErr('')
    const res = await requestDesktopCapability(cap)
    if (!res) setErr('The desktop app is no longer connected.')
    else if (!res.granted && res.reason) setErr(res.reason)
    setBusyCap('')
    refresh()
  }

  return (
    <Section
      title="Desktop capabilities"
      hint="Native permissions the Gideon desktop app holds on this machine. Each one is granted by macOS, not by Gideon — revoking it in System Settings takes effect immediately, and nothing here can grant itself."
    >
      {!ds.connected || caps.length === 0 ? (
        <div className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-high">
              <MonitorOff size={17} className="text-on-surface-low" />
            </span>
            <div className="min-w-0">
              <div data-type="label-s" className="text-on-surface" style={fvs(600)}>Desktop app not connected</div>
              <div data-type="body-s" className="mt-0.5 text-on-surface-low">
                You are viewing this in a browser tab. Native capabilities — microphone, notifications, the menu-bar item — exist only while the Gideon desktop app is running, so there is nothing to show or grant here.
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {caps.map(([cap, state]) => {
            const p = GRANT_PRESENTATION[state.granted] ?? GRANT_PRESENTATION.unavailable
            const label = capabilityLabel(cap)
            if (cap === 'login_item' && loginItemBridge) return <LoginItemRow key={cap} label={label} />
            return (
              <div key={cap} className="flex items-start justify-between gap-3 rounded-lg bg-surface-container px-4 py-3">
                <div className="min-w-0">
                  <div data-type="label-s" className="text-on-surface" style={fvs(600)}>{label}</div>
                  <div data-type="body-s" className={`mt-0.5 ${p.tone}`}>{p.label}</div>
                  {state.reason && (
                    <div data-type="body-s" className="mt-0.5 text-on-surface-low">{state.reason}</div>
                  )}
                </div>
                {state.requestable && (
                  <Button variant="secondary" size="sm" loading={busyCap === cap}
                    onClick={() => request(cap)}>
                    {
}
                    Allow {label.toLowerCase()}
                  </Button>
                )}
              </div>
            )
          })}
          {ds.shell && (
            <div data-type="caption" className="text-on-surface-low">
              Reported by the desktop app {ds.shell.version} on {ds.shell.platform}.
            </div>
          )}
          {err && <FieldError>{err}</FieldError>}
        </div>
      )}
    </Section>
  )
}

function EgressPolicyEditor() {
  const { data: eg, refresh } = useQuery(
    'settings:egress', () => api.securityEgress().catch(() => null), { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!eg) return null

  const save = async (next: EgressPolicyConfig) => {
    setBusy(true); setErr('')
    try { await api.setSecurityEgress(next); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy(false) }
  }
  const setAllowPrivate = async (next: boolean) => {
    if (next && !(await confirm({
      title: 'Allow all private networks?',
      body: 'The agent will be able to reach any private or LAN address, removing SSRF protection for the whole LAN.',
      confirmLabel: 'Allow private networks',
      danger: true,
    }))) return
    await save({ ...eg, allow_private: next })
  }

  return (
    <Section title="Network egress" hint="The agent's outbound fetches, scrapes, and webhooks are blocked from reaching non-public addresses (loopback, LAN, cloud metadata) by default — SSRF protection. Relax it for your own network below; a deny always wins over an allow.">
      <div className="flex flex-col gap-4">
        <HostList label="Allowed hosts" hint="Reachable even if they resolve to a private/LAN address (e.g. a homelab service). Bare domain covers subdomains."
          hosts={eg.allow_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, allow_hosts: hosts })} />
        <HostList label="Denied hosts" hint="Never reachable, even if public. Overrides an allow."
          hosts={eg.deny_hosts} disabled={busy}
          onChange={(hosts) => save({ ...eg, deny_hosts: hosts })} />
        <label className="flex items-start gap-2.5 rounded-lg bg-surface-container px-3 py-2.5 cursor-pointer">
          <input type="checkbox" checked={eg.allow_private} disabled={busy}
            onChange={(e) => { void setAllowPrivate(e.target.checked) }}
            className="mt-0.5 size-4 shrink-0 accent-primary" />
          <span className="min-w-0">
            <span data-type="body-s" className="text-on-surface">Allow all private networks</span>
            <span data-type="body-s" className="block text-on-surface-low">Permit egress to any private/LAN address, not just the allow-list. Only on a fully trusted network — this removes SSRF protection for the whole LAN.</span>
          </span>
        </label>
        {err && <FieldError>{err}</FieldError>}
      </div>
    </Section>
  )
}

export function hostRefusal(raw: string, hosts: string[]): string | null {
  const h = raw.trim().toLowerCase()
  if (!h) return null
  if (hosts.includes(h)) return `${h} is already listed.`
  if (h.includes('/') || h.includes(':') || h.includes(' ')) {
    return 'Enter a bare hostname — no scheme, port, or spaces (e.g. nas.local).'
  }
  return null
}

function HostList({ label, hint, hosts, disabled, onChange }: {
  label: string; hint: string; hosts: string[]; disabled: boolean; onChange: (hosts: string[]) => void
}) {
  const [draft, setDraft] = useState('')
  const [refused, setRefused] = useState('')
  const add = () => {
    const h = draft.trim().toLowerCase()
    if (!h) return
    const why = hostRefusal(draft, hosts)
    if (why) { setRefused(why); return }
    setRefused(''); onChange([...hosts, h]); setDraft('')
  }
  return (
    <div>
      <div data-type="body-s" className="mb-1 flex items-center gap-1.5 text-on-surface"><Globe size={13} className="text-on-surface-low" /> {label}</div>
      <div data-type="body-s" className="mb-2 text-on-surface-low">{hint}</div>
      <div className="flex flex-col gap-1.5">
        {hosts.map((h) => (
          <div key={h} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
            <code data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{h}</code>
            <button type="button" disabled={disabled} onClick={() => onChange(hosts.filter((x) => x !== h))}
              className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${h}`}>
              <X size={15} />
            </button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          {
}
          <input value={draft} disabled={disabled}
            aria-label={`Add a host to ${label.toLowerCase()}`}
            aria-invalid={refused ? true : undefined}
            onChange={(e) => { setDraft(e.target.value); if (refused) setRefused('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') add() }}
            placeholder="e.g. nas.local"
            data-type="body-s" className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
          <button type="button" onClick={add} data-type="body-s"
            {...unavailableWhen(!draft.trim(), 'Enter a host first', { busy: disabled })}
            className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
            <Plus size={15} /> Add
          </button>
        </div>
        {
}
        {refused && <FieldError>{refused}</FieldError>}
      </div>
    </div>
  )
}

function BaselineState({ baseline: b }: { baseline: DenylistBaseline }) {
  const chip = 'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5'
  const digest = <code className="tabular-nums" title={b.sha256}>{b.sha256.slice(0, 12)}…</code>
  if (b.verified) {
    return (
      <span role="status" data-type="caption" className={`${chip} bg-surface-container text-on-surface-low`}
        aria-label={`Baseline v${b.version} matches what shipped: ${b.count} patterns verified against the release sha256`}>
        <ShieldCheck size={13} className="text-primary" aria-hidden />
        <span>Baseline v{b.version} matches what shipped — {b.count} patterns, sha256 {digest}</span>
      </span>
    )
  }
  return (
    <span role="alert" data-type="caption" className={`${chip} border border-danger/30 bg-danger/5 text-danger`}
      aria-label={`Baseline v${b.version} does not match what shipped: ${b.detail || 'the packaged file diverged'}. The ${b.count} verified patterns are still enforced.`}>
      <ShieldAlert size={13} aria-hidden />
      <span>Baseline v{b.version} does NOT match what shipped — {b.detail || 'the packaged file diverged'}; the {b.count} verified patterns are still enforced (release sha256 {digest})</span>
    </span>
  )
}

function DeniedCommandsEditor({ builtin, user, baseline, userAdditions, onChange }: {
  builtin: string[]; user: string[]; baseline: DenylistBaseline; userAdditions: number; onChange: () => void
}) {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async (next: string[]) => {
    setBusy(true)
    setErr('')
    try {
      await api.setUserDeniedCommands(next)
      onChange()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setBusy(false)
    }
  }

  const add = async () => {
    const p = draft.trim()
    if (!p || user.includes(p)) { setDraft(''); return }
    try { new RegExp(p) } catch { setErr('Not a valid regular expression'); return }
    await save([...user, p])
    setDraft('')
  }

  return (
    <Section title="Shell denylist" hint="Regexes matched against every command the agent runs. The packaged baseline is always enforced and read-only; your patterns are added to it, never subtracted from it.">
      <div className="flex flex-col gap-4">
        <div>
          <div data-type="body-s" className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-on-surface-low">
            <span className="inline-flex items-center gap-1.5"><Lock size={13} aria-hidden /> Baseline ({builtin.length}) — always enforced, not editable here</span>
            <BaselineState baseline={baseline} />
          </div>
          <p data-type="body-s" className="mb-2 text-on-surface-low">
            The baseline ships with Gideon and is re-checked against the sha256
            recorded at release on every read, so nothing running inside the agent — the
            model included — can quietly shorten it. That catches drift and tampering from
            the inside; it is not a lock. Anyone who can edit the installed package before
            Gideon starts owns the baseline.
          </p>
          {
}
          <div className="max-h-44 overflow-y-auto rounded-lg bg-surface-container p-2"
            tabIndex={0} role="group" aria-label={`Baseline shell denylist patterns (${builtin.length}), read-only`}>
            {builtin.map((p) => (
              <code key={p} data-type="caption" className="block px-2 py-1 text-on-surface-low tabular-nums">{p}</code>
            ))}
          </div>
        </div>
        <div>
          {
}
          <div data-type="body-s" className="mb-0.5 text-on-surface">Your patterns</div>
          <div data-type="body-s" className="mb-2 text-on-surface-low">
            {userAdditions} user addition{userAdditions === 1 ? '' : 's'} on top of the baseline
            {user.length > userAdditions
              && ` · ${user.length - userAdditions} of your ${user.length} entries already match a baseline pattern and add nothing`}
          </div>
          <div className="flex flex-col gap-1.5">
            {user.map((p) => (
              <div key={p} className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                <code data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{p}</code>
                <button type="button" disabled={busy} onClick={() => save(user.filter((x) => x !== p))}
                  className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface" aria-label={`Remove ${p}`}>
                  <X size={15} />
                </button>
              </div>
            ))}
            <div className="flex items-center gap-2">
              {
}
              <input
                value={draft}
                aria-label="Add a shell denylist pattern (regex)"
                onChange={(e) => { setDraft(e.target.value); setErr('') }}
                onKeyDown={(e) => { if (e.key === 'Enter') add() }}
                placeholder="e.g. my-secret-tool .*"
                data-type="body-s" className="min-w-0 flex-1 rounded-lg bg-surface-container px-3 py-2 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary"
              />
              <button type="button" onClick={add} data-type="body-s"
                {...unavailableWhen(!draft.trim(), 'Enter a pattern first', { busy })}
                className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-primary px-3 py-2 text-on-primary disabled:opacity-50 aria-disabled:opacity-50 aria-disabled:cursor-not-allowed">
                <Plus size={15} /> Add
              </button>
            </div>
            {err && <FieldError>{err}</FieldError>}
          </div>
        </div>
      </div>
    </Section>
  )
}
