import { useEffect, useState } from 'react'
import { Activity, Ban, CheckCircle2, FileClock, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import {
  api,
  ApiError,
  type HypermidDiagnosticsWire,
  type HypermidLogPageWire,
} from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { PanelHeader, Section } from '../settings/settingsUI'
import {
  MAINTENANCE_ACTIONS,
  decideMaintenanceApply,
  receiptIsTerminal,
  receiptProgress,
  rememberedMaintenanceJob,
  rememberMaintenanceJob,
  type MaintenanceAction,
  type MaintenancePlan,
  type MaintenanceReceipt,
} from './operationsState'

function failureMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

function receiptError(receipt: MaintenanceReceipt): string {
  const message = receipt.error?.message
  if (typeof message === 'string') return message
  const code = receipt.error?.code
  return typeof code === 'string' ? code.replaceAll('_', ' ') : ''
}

function statusTone(state: string): 'ok' | 'warn' | 'muted' {
  if (['healthy', 'committed'].includes(state)) return 'ok'
  if (['degraded', 'running', 'outcome_unknown'].includes(state)) return 'warn'
  if (['unhealthy', 'failed'].includes(state)) return 'warn'
  return 'muted'
}

function Diagnostics() {
  const query = useQuery('hypermid:operations:diagnostics', () => api.hypermidDiagnostics())
  const [observed, setObserved] = useState<HypermidDiagnosticsWire>()
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (query.data) setObserved(query.data) }, [query.data])
  const rerun = async () => {
    setRefreshing(true); setError('')
    try { setObserved(await api.hypermidDiagnostics(true)) }
    catch (caught) { setError(failureMessage(caught, 'Diagnostics could not be refreshed.')) }
    finally { setRefreshing(false) }
  }
  if (!observed && query.error) return <LoadError what="Hypermid diagnostics" error={query.error} onRetry={query.refresh} />
  if (!observed) return <FormSkeleton sections={1} rows={3} what="Hypermid diagnostics" />
  return <Section title="Diagnostics" hint="A rerun leaves the last observation visible until a complete replacement arrives."
    right={<Button size="sm" variant="secondary" loading={refreshing} onClick={() => void rerun()}><RefreshCw size={14} /> Rerun checks</Button>}>
    <div className="mb-m flex flex-wrap items-center gap-s text-sm text-on-surface-low">
      <StatusPill label={observed.cached ? 'cached observation' : 'fresh observation'} tone={observed.cached ? 'warn' : 'ok'} />
      <span>Observed {new Date(observed.observed_at).toLocaleString()}</span>
      <span>Cursor {observed.cursor.epoch}:{observed.cursor.sequence}</span>
    </div>
    <div className="grid gap-m">{observed.checks.map((check) => <Surface key={check.id} tone="container" radius="lg" className="p-m">
      <div className="flex flex-wrap items-center gap-s"><Activity size={15} aria-hidden className="text-primary" />
        <h3 className="text-sm text-on-surface">{check.title}</h3>
        <StatusPill label={check.health} tone={statusTone(check.health)} />
        {check.cached && <StatusPill label="cached" tone="warn" />}
      </div>
      <p className="mt-xs text-sm text-on-surface-low">{check.summary}</p>
      {check.next_action && <p className="mt-s text-sm text-on-surface"><span className="text-on-surface-low">Next action: </span>{check.next_action}</p>}
      <p data-type="caption" className="mt-s text-on-surface-low">Observed {new Date(check.observed_at).toLocaleString()}</p>
    </Surface>)}</div>
    {error && <p role="alert" className="mt-m text-sm text-danger">{error}</p>}
  </Section>
}

function Logs() {
  const query = useQuery('hypermid:operations:logs', () => api.hypermidLogs({ limit: 100 }))
  const [page, setPage] = useState<HypermidLogPageWire>()
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { if (query.data) setPage(query.data) }, [query.data])
  const resume = async () => {
    setRefreshing(true); setError('')
    try {
      const next = await api.hypermidLogs({ after: page?.cursor, limit: 100 })
      setPage((current) => next.gap || !current ? next : {
        ...next,
        entries: [...current.entries, ...next.entries].slice(-200),
      })
    } catch (caught) { setError(failureMessage(caught, 'Logs could not be resumed.')) }
    finally { setRefreshing(false) }
  }
  if (!page && query.error) return <LoadError what="Hypermid logs" error={query.error} onRetry={query.refresh} />
  return <Section title="Live logs" hint="Refresh resumes after the last accepted cursor; a retention gap is reported explicitly."
    right={<Button size="sm" variant="secondary" loading={refreshing} onClick={() => void resume()}><RefreshCw size={14} /> Resume logs</Button>}>
    {page?.gap && <p role="alert" className="mb-m rounded-lg border border-warn/40 bg-warn/10 p-m text-sm text-on-surface">
      The stored cursor is outside retained logs. Resume from {page.recovery_cursor ? `${page.recovery_cursor.epoch}:${page.recovery_cursor.sequence}` : 'the reported recovery point'}.
    </p>}
    {!page ? <FormSkeleton sections={1} rows={3} what="Hypermid logs" />
      : page.entries.length === 0 ? <p className="text-sm text-on-surface-low">No log entries are available after this cursor.</p>
      : <div className="grid max-h-96 gap-xs overflow-y-auto" aria-label="Redacted Hypermid log entries">{page.entries.map((entry) => <div key={`${entry.cursor.epoch}:${entry.cursor.sequence}`} className="grid min-w-0 gap-xs rounded-lg bg-surface-container px-m py-s text-xs sm:grid-cols-[auto_auto_1fr]">
        <span className="text-on-surface-low">{new Date(entry.observed_at).toLocaleTimeString()}</span>
        <span className="text-on-surface-low">{entry.component} · {entry.severity}</span>
        <span className="min-w-0 break-words text-on-surface">{entry.message}</span>
      </div>)}</div>}
    {error && <p role="alert" className="mt-m text-sm text-danger">{error}</p>}
  </Section>
}

function PlanReview({ plan, reviewed, destructiveConfirmed, onReviewed, onDestructive, onApply, busy }: {
  plan: MaintenancePlan
  reviewed: boolean
  destructiveConfirmed: boolean
  onReviewed: (value: boolean) => void
  onDestructive: (value: boolean) => void
  onApply: () => void
  busy: boolean
}) {
  const decision = decideMaintenanceApply(plan, reviewed ? plan.plan_digest : '', destructiveConfirmed)
  return <div role="dialog" aria-modal="false" aria-labelledby="maintenance-plan-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div>
      <h3 id="maintenance-plan-title" className="text-base text-on-surface">Review maintenance plan</h3>
      <p className="mt-xs text-sm text-on-surface-low">Expires {new Date(plan.expires_at).toLocaleString()}</p>
    </div><div className="flex gap-s">{plan.restart_required && <StatusPill label="restart required" tone="warn" />}
      {plan.destructive && <StatusPill label="destructive" tone="warn" />}</div></div>
    <ol className="mt-m grid gap-s">{plan.steps.map((step, index) => <li key={step.id} className="rounded-lg bg-surface-container p-m">
      <div className="flex flex-wrap items-center gap-s"><span className="text-xs text-on-surface-low">{index + 1}</span>
        <span className="text-sm text-on-surface">{step.title}</span><StatusPill label={step.effect.replaceAll('_', ' ')} tone={step.effect === 'read' ? 'muted' : 'warn'} /></div>
      {step.detail && <p className="mt-xs text-sm text-on-surface-low">{step.detail}</p>}
    </li>)}</ol>
    {plan.blockers.length > 0 && <div role="alert" className="mt-m rounded-lg border border-danger/40 bg-danger/10 p-m">
      <p className="text-sm text-on-surface">This plan cannot be applied:</p><ul className="mt-s list-disc pl-l text-sm text-on-surface-low">
        {plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
    </div>}
    <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Plan digest {plan.plan_digest}</p>
    <label className="hypermid-touch mt-m flex cursor-pointer items-center gap-s text-sm text-on-surface">
      <input type="checkbox" checked={reviewed} onChange={(event) => onReviewed(event.target.checked)} className="size-4 accent-primary" />I reviewed this exact plan and digest.
    </label>
    {plan.destructive && <label className="hypermid-touch mt-s flex cursor-pointer items-center gap-s text-sm text-on-surface">
      <input type="checkbox" checked={destructiveConfirmed} onChange={(event) => onDestructive(event.target.checked)} className="size-4 accent-primary" />I confirm the listed destructive effects.
    </label>}
    {!decision.allowed && <p className="mt-s text-sm text-on-surface-low">{decision.reason}</p>}
    <div className="hypermid-action-bar mt-m flex justify-end"><Button size="sm" disabled={!decision.allowed} loading={busy} onClick={onApply}><ShieldCheck size={14} /> Apply reviewed plan</Button></div>
  </div>
}

function Receipt({ receipt, busy, onRefresh, onCancel }: {
  receipt: MaintenanceReceipt
  busy: string
  onRefresh: () => void
  onCancel: () => void
}) {
  const progress = receiptProgress(receipt)
  const failure = receiptError(receipt)
  return <div aria-live={receiptIsTerminal(receipt) ? 'polite' : undefined}><Surface tone="container" radius="lg" className="mt-m p-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div>
      <div className="flex flex-wrap items-center gap-s"><FileClock size={16} className="text-primary" aria-hidden />
        <h3 className="text-sm text-on-surface">Maintenance receipt</h3><StatusPill label={receipt.state.replaceAll('_', ' ')} tone={statusTone(receipt.state)} /></div>
      <p data-type="caption" className="mt-xs text-on-surface-low">Job {receipt.job_id} · {progress.complete} of {progress.total} steps terminal
        {receipt.cursor ? ` · cursor ${receipt.cursor.epoch}:${receipt.cursor.sequence}` : ''}</p>
    </div><div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" loading={busy === 'refresh'} onClick={onRefresh}><RefreshCw size={14} /> Check authoritative status</Button>
      {receipt.state === 'running' && <Button size="sm" variant="danger" loading={busy === 'cancel'} onClick={onCancel}><Ban size={14} /> Cancel</Button>}</div></div>
    <div className="mt-m grid gap-s">{receipt.steps.map((step) => <div key={step.id} className="flex flex-wrap items-center justify-between gap-s rounded-lg bg-surface px-m py-s text-sm">
      <span className="text-on-surface">{step.title}</span><StatusPill label={step.state} tone={statusTone(step.state)} />
    </div>)}</div>
    {receipt.state === 'outcome_unknown' && <p role="alert" className="mt-m text-sm text-warn">The operation may have taken effect. Check authoritative status before any retry.</p>}
    {failure && <p role="alert" className="mt-m text-sm text-danger">{failure}</p>}
    {receipt.rollback_available && <p className="mt-m flex items-center gap-s text-sm text-on-surface"><CheckCircle2 size={15} className="text-primary" /> Verified rollback material is available.</p>}
    {receipt.artifact_digest && <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Artifact digest {receipt.artifact_digest}</p>}
  </Surface></div>
}

function Maintenance() {
  const [action, setAction] = useState<MaintenanceAction>('integrity_check')
  const [plan, setPlan] = useState<MaintenancePlan>()
  const [receipt, setReceipt] = useState<MaintenanceReceipt>()
  const [reviewed, setReviewed] = useState(false)
  const [destructiveConfirmed, setDestructiveConfirmed] = useState(false)
  const [jobId, setJobId] = useState(rememberedMaintenanceJob)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const acceptReceipt = (next: MaintenanceReceipt) => {
    setReceipt(next); setJobId(next.job_id); rememberMaintenanceJob(next.job_id)
  }
  const review = async () => {
    setBusy('plan'); setError(''); setPlan(undefined); setReviewed(false); setDestructiveConfirmed(false)
    try { setPlan(await api.planHypermidMaintenance(action)) }
    catch (caught) { setError(failureMessage(caught, 'The maintenance plan could not be prepared.')) }
    finally { setBusy('') }
  }
  const apply = async () => {
    if (!plan) return
    const decision = decideMaintenanceApply(plan, reviewed ? plan.plan_digest : '', destructiveConfirmed)
    if (!decision.allowed) { setError(decision.reason); return }
    setBusy('apply'); setError('')
    try { acceptReceipt(await api.applyHypermidMaintenance(plan.plan_id, plan.plan_digest, destructiveConfirmed)); setPlan(undefined) }
    catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setPlan(undefined); setReviewed(false)
        setError('The reviewed plan is stale or expired. Prepare and review a new plan before applying.')
      } else setError(failureMessage(caught, 'Maintenance was refused.'))
    } finally { setBusy('') }
  }
  const refreshReceipt = async (requestedJob = jobId) => {
    const target = requestedJob.trim()
    if (!target) { setError('Enter a maintenance job ID to recover its receipt.'); return }
    setBusy('refresh'); setError('')
    try { acceptReceipt(await api.hypermidMaintenanceStatus(target)) }
    catch (caught) { setError(failureMessage(caught, 'The maintenance receipt could not be recovered.')) }
    finally { setBusy('') }
  }
  const cancel = async () => {
    if (!receipt) return
    setBusy('cancel'); setError('')
    try { acceptReceipt(await api.cancelHypermidMaintenance(receipt.job_id)) }
    catch (caught) { setError(failureMessage(caught, 'Maintenance could not be cancelled.')) }
    finally { setBusy('') }
  }
  useEffect(() => {
    if (!receipt || receipt.state !== 'running') return
    const timer = window.setInterval(() => { void refreshReceipt(receipt.job_id) }, 2_000)
    return () => window.clearInterval(timer)
  }, [receipt?.job_id, receipt?.state])

  const selected = MAINTENANCE_ACTIONS.find((item) => item.id === action)!
  return <Section title="Maintenance" hint="Every mutation begins with an expiring daemon plan and ends with an authoritative receipt.">
    <div className="grid gap-m sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end"><label className="grid gap-xs text-sm text-on-surface">
      Action<select value={action} onChange={(event) => { setAction(event.target.value as MaintenanceAction); setPlan(undefined); setReviewed(false) }}
        className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
        {MAINTENANCE_ACTIONS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
      <span data-type="caption" className="text-on-surface-low">{selected.detail}</span></label>
      <Button size="sm" variant="secondary" loading={busy === 'plan'} onClick={() => void review()}>Review plan</Button></div>
    {plan && <PlanReview plan={plan} reviewed={reviewed} destructiveConfirmed={destructiveConfirmed} onReviewed={setReviewed}
      onDestructive={setDestructiveConfirmed} busy={busy === 'apply'} onApply={() => void apply()} />}
    <div className="mt-l rounded-lg border border-outline-variant p-m"><label className="grid gap-xs text-sm text-on-surface">
      Recover a receipt<div className="flex flex-wrap gap-s"><input value={jobId} onChange={(event) => setJobId(event.target.value)} aria-label="Maintenance job ID"
        className="min-h-11 min-w-0 flex-1 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" />
      <Button size="sm" variant="secondary" loading={busy === 'refresh'} onClick={() => void refreshReceipt()}>Recover receipt</Button></div></label></div>
    {receipt && <Receipt receipt={receipt} busy={busy} onRefresh={() => void refreshReceipt(receipt.job_id)} onCancel={() => void cancel()} />}
    {error && <p role="alert" className="mt-m flex items-start gap-s text-sm text-danger"><TriangleAlert size={16} className="mt-0.5 shrink-0" />{error}</p>}
  </Section>
}

export function Operations() {
  return <div>
    <PanelHeader title="Operations" hint="Inspect current evidence, resume redacted logs, and run reviewed maintenance against the authenticated project scope." />
    <Diagnostics />
    <Maintenance />
    <Logs />
  </div>
}
