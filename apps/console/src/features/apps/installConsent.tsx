import { useState } from 'react'
import { SCAN_FINDINGS_SHOWN, hiddenFindingsNote, ruleGloss } from '../../shared/data/scanFindings'
import { trustTierLabel } from '../../shared/data/trustTier'
import { ShieldAlert, ShieldCheck, ShieldQuestion, BadgeCheck, AlertTriangle, Terminal, CalendarClock, Bot, Globe, Copy, Check } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { Modal } from '../../shared/ui/Modal'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import type { AppSummary, AppInstallResult, AppCronSummary, AppScanReport } from '../../shared/data/api'
import { terminalRefusalReason, type GuardedResult } from '../../shared/data/useGuardedInstall'
import { copyText } from '../../app/shell/clipboard'


const sentence = (s: string) => (/[.!?…]$/.test(s.trim()) ? s.trim() : `${s.trim()}.`)

function SignatureRow({ signature, verdict, tier }: {
  signature: NonNullable<AppScanReport['signature']>
  verdict: string
  tier?: string
}) {
  const s = signature.state
  const blocked = verdict === 'dangerous'
  const tone = s === 'invalid' ? 'text-danger' : s === 'signed' ? 'text-ok' : 'text-on-surface-low'
  const Icon = s === 'invalid' ? ShieldAlert : s === 'signed' ? BadgeCheck : ShieldQuestion
  const label =
    s === 'signed' ? `Signed by ${signature.signer || 'a trusted key'}`
      : s === 'invalid' ? 'Invalid signature — install refused'
        : `Unsigned — ${trustTierLabel(tier)}`
  return (
    <div className="mt-2 flex flex-col gap-1">
      <div className={`flex items-center gap-2 ${tone}`} data-type="body-m">
        <Icon size={16} /> {label}
      </div>
      {s === 'invalid' && signature.reason && (
        <div data-type="body-s" className="text-danger">{signature.reason}</div>
      )}
      {
}
      {s === 'unsigned' && (
        <div data-type="body-s" className="text-on-surface-low">
          No maintainer signature, so Gideon can't confirm who published this.{' '}
          {blocked
            ? 'That is not why this install was refused — the security scan is, and it cannot be overridden.'
            : 'It still installs — the security scan above is what gates it.'}
        </div>
      )}
    </div>
  )
}

export function ScanReport({ scan }: { scan: NonNullable<AppInstallResult['scan']> }) {
  const v = scan.verdict
  const tone = v === 'dangerous' ? 'text-danger' : v === 'warning' ? 'text-warn' : 'text-ok'
  const Icon = v === 'clean' ? ShieldCheck : v === 'dangerous' ? ShieldAlert : AlertTriangle
  return (
    <div className="rounded-md border border-outline-variant bg-surface-high p-m">
      <div className={`flex items-center gap-2 ${tone}`} data-type="body-m"><Icon size={16} /> Security scan: {v}
        {scan.findings.length > 0 && ` · ${scan.findings.length} finding${scan.findings.length === 1 ? '' : 's'}`}
      </div>
      {scan.signature && <SignatureRow signature={scan.signature} verdict={v} tier={scan.tier} />}
      {scan.findings.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {
}
          {scan.findings.slice(0, SCAN_FINDINGS_SHOWN).map((f, i) => (
            <li key={i} data-type="body-s" className="text-on-surface-low">
              <span className="text-on-surface">{f.rule}</span> ({f.severity})
              {f.path ? ` — ${f.path}` : ''}{f.evidence ? `: ${f.evidence}` : ''}
              {ruleGloss(f.rule) && (
                <span className="block text-on-surface-var">{ruleGloss(f.rule)}</span>
              )}
            </li>
          ))}
          {
}
          {hiddenFindingsNote(scan.findings.length) && (
            <li data-type="body-s" className="text-on-surface-low italic">
              {hiddenFindingsNote(scan.findings.length)}
            </li>
          )}
        </ul>
      )}
      {v === 'dangerous' && <div data-type="body-s" className="mt-2 text-danger">This app is blocked — dangerous content cannot be installed.</div>}
    </div>
  )
}

// Exported so the onboarding essential-apps step consents through THIS surface rather
export type AppHookSummary = { name: string; event: string; provider: string }

export function ConsentModal({ label, result, busy, permissions, crons, hooks, appUI, onConfirm, onClose }: {
  label: string; result: GuardedResult; busy: boolean
  permissions: AppSummary['permissions'] | undefined
  crons: AppCronSummary[] | undefined
  hooks?: AppHookSummary[]
  appUI?: { hasUI?: boolean; uiComponents?: string }
  onConfirm: () => void; onClose: () => void
}) {
  if (result.clientInstall) {
    return (
      <Modal title={`Install ${label}`} icon={<Terminal size={18} />} onClose={onClose}>
        <div className="flex flex-col gap-m p-l" style={{ minWidth: 460 }}>
          {
}
          <p data-type="body-s" className="text-on-surface-low">
            {sentence(result.error || 'This app installs on your local machine, not this server.')} Run this in your terminal:
          </p>
          {result.clientInstall.shell && <ClientInstallCommand label="Install command" cmd={result.clientInstall.shell} />}
          {result.clientInstall.postInstall && <ClientInstallCommand label="Then" cmd={result.clientInstall.postInstall} />}
          <p data-type="label-s" className="text-on-surface-low">
            The command runs on your machine, outside Gideon's security scanner — review it before running.
          </p>
          <div className="flex justify-end gap-2 pt-s">
            <Button variant="ghost" onClick={onClose}>Done</Button>
          </div>
        </div>
      </Modal>
    )
  }
  const refusal = terminalRefusalReason(result)
  return (
    <Modal title={`Install ${label}`} icon={<ShieldAlert size={18} />} onClose={onClose}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <p data-type="body-s" className="text-on-surface-low">
          {refusal
            || 'The security scanner raised warnings. Review the findings — you can install anyway if you trust the source.'}
        </p>
        {result.scan && <ScanReport scan={result.scan} />}
        {
}
        <PermissionConsent permissions={permissions} appUI={appUI} />
        {!!(hooks ?? result.hooks)?.length && <div data-type="body-s" className="text-on-surface-low">
          <div data-type="label-m" className="text-on-surface">Lifecycle hooks</div>
          {(hooks ?? result.hooks ?? []).map((hook) => <div key={hook.name}>{hook.name} · {hook.event} via {hook.provider}</div>)}
        </div>}
        {(crons ?? []).length > 0 && <CronConsentList crons={crons!} />}
        <div className="flex justify-end gap-2 pt-s">
          {
}
          <Button variant="ghost" onClick={onClose}>{refusal ? 'Done' : 'Cancel'}</Button>
          {!refusal && (
            <Button variant="primary" loading={busy} onClick={onConfirm}><ShieldAlert size={16} /> Install anyway
            </Button>
          )}
        </div>
      </div>
    </Modal>
  )
}

function ClientInstallCommand({ label, cmd }: { label: string; cmd: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(cmd, 'the command')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  return (
    <div>
      <div data-type="label-s" className="mb-1 text-on-surface-low uppercase tracking-wide">{label}</div>
      <div className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-pre font-mono text-[0.75rem] text-on-surface">{cmd}</code>
        <SquareIconButton label="Copy command" title={copied ? 'Copied' : 'Copy'} onClick={copy} className="shrink-0">
          {copied ? <Check size={14} /> : <Copy size={14} />}
        </SquareIconButton>
      </div>
    </div>
  )
}

function describeMessagingTarget(pattern: string): string {
  if (pattern === '*') return 'any installed app'
  if (pattern.endsWith('*')) return `any app whose name starts with “${pattern.slice(0, -1)}”`
  return pattern
}

function networkClaim(network: boolean | undefined): string {
  if (network === undefined) return 'not declared'
  return network ? 'declared' : 'declared as denied'
}

export function PermissionList({ perms, appUI }: {
  perms: AppSummary['permissions']
  appUI?: { hasUI?: boolean; uiComponents?: string }
}) {
  const rows: string[] = []
  if (perms.api?.length) rows.push(`API: ${perms.api.join(', ')}`)
  if (perms.events?.length) rows.push(`Events: ${perms.events.join(', ')}`)
  if (perms.mcpTools?.length) rows.push(`MCP tools: ${perms.mcpTools.join(', ')}`)
  if (perms.memory) rows.push(`Memory: ${perms.memory}`)
  if (perms.storage) rows.push('Storage')
  if (perms.cron) rows.push('Scheduled jobs')
  if (perms.agent) rows.push('Run background agents')
  const messaging = perms.appMessaging ?? []
  if (messaging.length) {
    rows.push(`App messaging: ${messaging.map(describeMessagingTarget).join(', ')}`)
  }
  if (perms.storageShared) rows.push('Shares its data with apps you grant read access')
  const sharedReads = perms.storageRead ?? []
  if (sharedReads.length) {
    rows.push(`Reads other apps' data (read-only): ${sharedReads.map(describeMessagingTarget).join(', ')}`)
  }
  const desktopCaps = perms.desktop ?? []
  if (desktopCaps.length) {
    rows.push(`Desktop capabilities: ${desktopCaps.map((c) => c.replace(/_/g, ' ')).join(', ')}`)
  }
  const proposalKinds = perms.proposals ?? []
  if (proposalKinds.length) {
    rows.push(
      `Can ask you to approve: ${proposalKinds.map((p) => p.label || p.kind_suffix).join(', ')}`,
    )
  }
  const declaredEvents = perms.eventSubscriptions ?? []
  if (declaredEvents.length) {
    rows.push(`Receive platform events: ${declaredEvents.join(', ')}`)
  }
  if (perms.backgroundTasks) rows.push('Run a long-lived background worker')
  return (
    <div>
      <div data-type="label-m" className="mb-1 text-on-surface">Permissions the gateway enforces</div>
      {rows.length === 0 ? <div data-type="body-s" className="text-on-surface-low">None — this app is granted no gateway capability.</div> : (
        <ul className="flex flex-col gap-1">
          {rows.map((r, i) => <li key={i} data-type="body-s" className="text-on-surface-low">• {r}</li>)}
        </ul>
      )}
      {messaging.length === 0 && (
        <div data-type="body-s" className="mt-1 text-on-surface-low">
          App messaging: none — it declared no target, and the gateway broker is the only
          way one app can reach another, so it can message no other app.
        </div>
      )}
      {
}
      {desktopCaps.length === 0 && (
        <div data-type="body-s" className="mt-1 text-on-surface-low">
          Desktop capabilities: none — it declared no native capability, and the gateway
          mediates every app→desktop call, so it can reach nothing native on this machine.
        </div>
      )}
      {(appUI?.hasUI || appUI?.uiComponents) && (
        <div className="mt-2 flex gap-2 rounded-md border border-outline-variant bg-surface-high p-m">
          <Globe size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
          <div data-type="body-s" className="text-on-surface-low">
            <span className="text-on-surface">Host-page access: advisory only.</span>
            {' This app\'s UI runs in Gideon\'s origin, not an isolated origin. Its frontend code can access the host DOM, session cookie, and same-origin APIs.'}
          </div>
        </div>
      )}
      <div className="mt-2 flex gap-2 rounded-md border border-outline-variant bg-surface-high p-m">
        <Globe size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-on-surface-low" />
        <div data-type="body-s" className="text-on-surface-low">
          <span className="text-on-surface">Network access: {networkClaim(perms.network)}</span>
          {' — advisory only. Gideon does not confine an app\'s outbound traffic: this app\'s '}
          code can reach the network either way. The declaration is disclosure, not containment.
        </div>
      </div>
    </div>
  )
}

export function PermissionConsent({ permissions, appUI }: {
  permissions: AppSummary['permissions'] | undefined
  appUI?: { hasUI?: boolean; uiComponents?: string }
}) {
  return permissions === undefined ? (
    <div data-type="body-s" className="text-on-surface-low">
      Gideon could not read this app's declared permissions before installing —
      its manifest is fetched as part of the install. Open the app in the Store to see
      them, or review them on its page once installed.
    </div>
  ) : <PermissionList perms={permissions} appUI={appUI} />
}

function fmtCadence(c: AppCronSummary): string {
  if (c.cron_expr) return c.cadence || c.cron_expr
  const s = c.every ?? 0
  if (!s) return 'on a schedule'
  if (s % 86400 === 0) { const d = s / 86400; return `every ${d === 1 ? 'day' : `${d} days`}` }
  if (s % 3600 === 0) { const h = s / 3600; return `every ${h === 1 ? 'hour' : `${h} hours`}` }
  if (s % 60 === 0) { const m = s / 60; return `every ${m === 1 ? 'minute' : `${m} minutes`}` }
  return `every ${s}s`
}

function cadenceTitle(c: AppCronSummary): string | undefined {
  if (!c.cron_expr || !c.cadence) return undefined
  return `cron: ${c.cron_expr}`
}

export function CronConsentList({ crons }: { crons: AppCronSummary[] }) {
  return (
    <div>
      <div data-type="label-m" className="mb-1 flex items-center gap-1.5 text-on-surface">
        <CalendarClock size={14} /> Scheduled jobs
      </div>
      <div data-type="body-s" className="mb-2 text-on-surface-low">
        This app runs {crons.length === 1 ? 'a background agent' : `${crons.length} background agents`} on a schedule once installed.
      </div>
      <ul className="flex flex-col gap-1.5">
        {crons.map((c, i) => (
          <li key={c.name || i} className="rounded-md border border-outline-variant bg-surface-high p-m">
            <div className="flex items-center justify-between gap-2">
              <span data-type="body-s" className="text-on-surface">{c.name || 'job'}</span>
              <span data-type="label-s" className="shrink-0 text-on-surface-low" title={cadenceTitle(c)}>{fmtCadence(c)}</span>
            </div>
            {(c.agent || c.message) && (
              <div className="mt-1 flex items-start gap-1.5 text-on-surface-low" data-type="label-s">
                <Bot size={12} className="mt-0.5 shrink-0" />
                <span className="min-w-0">
                  {c.agent && <span className="text-on-surface-var">{c.agent}</span>}
                  {c.agent && c.message && ' — '}
                  {c.message && <span className="line-clamp-2">{c.message}</span>}
                </span>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
