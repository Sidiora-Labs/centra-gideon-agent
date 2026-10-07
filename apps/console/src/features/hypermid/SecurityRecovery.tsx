import { useState } from 'react'
import { ArchiveRestore, FileCheck2, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { api, ApiError, type HypermidSecurityPlanWire, type HypermidSecurityReceiptWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { PanelHeader, Row, RowGroup, Section } from '../settings/settingsUI'
import {
  initialSecurityRecoveryDraft,
  receiptNeedsRecovery,
  rememberedSecurityRecoveryJob,
  rememberSecurityRecoveryJob,
  securityRecoveryApplyDecision,
  securityRecoveryPlanBody,
  validateSecurityRecoveryDraft,
  type SecurityRecoveryAction,
  type SecurityRecoveryDraft,
} from './securityRecoveryState'

function message(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

function tone(state: string): 'ok' | 'warn' | 'muted' {
  if (state === 'committed') return 'ok'
  if (state === 'failed' || state === 'outcome_unknown' || state === 'unknown') return 'warn'
  return 'muted'
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

function PlanReview({ plan, reviewed, confirmed, busy, onReviewed, onConfirmed, onApply }: {
  plan: HypermidSecurityPlanWire
  reviewed: boolean
  confirmed: boolean
  busy: boolean
  onReviewed: (value: boolean) => void
  onConfirmed: (value: boolean) => void
  onApply: () => void
}) {
  const decision = securityRecoveryApplyDecision(plan, reviewed ? plan.plan_digest : '', confirmed)
  return <div role="dialog" aria-modal="false" aria-labelledby="security-recovery-plan-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div>
      <h3 id="security-recovery-plan-title" className="text-base text-on-surface">Review encrypted {plan.destructive ? 'restore' : 'backup'} plan</h3>
      <p className="mt-xs text-sm text-on-surface-low">Expires {new Date(plan.expires_at).toLocaleString()}</p></div>
      <div className="flex gap-s">{plan.destructive && <StatusPill label="destructive" tone="warn" />}{plan.restart_required && <StatusPill label="restart required" tone="warn" />}</div></div>
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Authority digest</dt><dd className="break-all font-mono text-on-surface">{plan.authority_digest}</dd></div>
      <div><dt className="text-on-surface-low">Parameters digest</dt><dd className="break-all font-mono text-on-surface">{plan.params_digest}</dd></div>
      {plan.source_digest && <div><dt className="text-on-surface-low">Validated source digest</dt><dd className="break-all font-mono text-on-surface">{plan.source_digest}</dd></div>}
      {plan.export_id && <div><dt className="text-on-surface-low">Export ID</dt><dd className="break-words text-on-surface">{plan.export_id}</dd></div>}
      {plan.stream_digest && <div><dt className="text-on-surface-low">Stream digest</dt><dd className="break-all font-mono text-on-surface">{plan.stream_digest}</dd></div>}
      {plan.bundle_digest && <div><dt className="text-on-surface-low">Bundle digest</dt><dd className="break-all font-mono text-on-surface">{plan.bundle_digest}</dd></div>}
      {plan.item_count != null && <div><dt className="text-on-surface-low">Items</dt><dd className="text-on-surface">{plan.item_count.toLocaleString()}</dd></div>}
      {plan.record_count != null && <div><dt className="text-on-surface-low">Records</dt><dd className="text-on-surface">{plan.record_count.toLocaleString()}</dd></div>}
      {plan.unresolved_effect_count != null && <div><dt className="text-on-surface-low">Unresolved effects</dt><dd className="text-on-surface">{plan.unresolved_effect_count.toLocaleString()}</dd></div>}
      {plan.unknown_effect_count != null && <div><dt className="text-on-surface-low">Unknown effects</dt><dd className="text-on-surface">{plan.unknown_effect_count.toLocaleString()}</dd></div>}
      {plan.cursor && <div><dt className="text-on-surface-low">Cursor</dt><dd className="text-on-surface">{plan.cursor.epoch}:{plan.cursor.sequence}</dd></div>}
    </dl>
    <ol className="mt-m grid gap-s">{plan.steps.map((step, index) => <li key={step.id} className="rounded-lg bg-surface-container p-m">
      <div className="flex flex-wrap items-center gap-s"><span className="text-xs text-on-surface-low">{index + 1}</span><span className="text-sm text-on-surface">{step.title}</span>
        <StatusPill label={step.effect.replaceAll('_', ' ')} tone={step.effect === 'read' ? 'muted' : 'warn'} /></div></li>)}</ol>
    {plan.blockers.length > 0 && <div role="alert" className="mt-m rounded-lg border border-danger/40 bg-danger/10 p-m text-sm text-danger">
      <p>This plan cannot be applied:</p><ul className="mt-s list-disc pl-l">{plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
    <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Plan digest {plan.plan_digest}</p>
    <label className="hypermid-touch mt-m flex cursor-pointer items-center gap-s text-sm text-on-surface"><input type="checkbox" checked={reviewed} onChange={(event) => onReviewed(event.target.checked)} className="size-4 accent-primary" />I reviewed this exact scope, source evidence, steps, and digest.</label>
    {plan.destructive && <label className="hypermid-touch mt-s flex cursor-pointer items-center gap-s text-sm text-on-surface"><input type="checkbox" checked={confirmed} onChange={(event) => onConfirmed(event.target.checked)} className="size-4 accent-primary" />I confirm this restore may replace authoritative memory.</label>}
    {!decision.allowed && <p className="mt-s text-sm text-on-surface-low">{decision.reason}</p>}
    <div className="hypermid-action-bar mt-m flex justify-end"><Button size="sm" disabled={!decision.allowed} disabledReason={!decision.allowed ? decision.reason : undefined} loading={busy} onClick={onApply}><ShieldCheck size={14} /> Apply reviewed plan</Button></div>
  </div>
}

function Receipt({ receipt, busy, onStatus, onRecover }: {
  receipt: HypermidSecurityReceiptWire
  busy: string
  onStatus: () => void
  onRecover: () => void
}) {
  return <Surface tone="container" radius="lg" className="mt-m p-l" aria-live="polite">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><div className="flex flex-wrap items-center gap-s"><FileCheck2 size={16} className="text-primary" />
      <h3 className="text-sm text-on-surface">Security operation receipt</h3><StatusPill label={receipt.state.replaceAll('_', ' ')} tone={tone(receipt.state)} />
      <StatusPill label={`effect ${receipt.effect_state.replaceAll('_', ' ')}`} tone={tone(receipt.effect_state)} /></div>
      <p data-type="caption" className="mt-xs text-on-surface-low">Job {receipt.job_id}{receipt.cursor ? ` · cursor ${receipt.cursor.epoch}:${receipt.cursor.sequence}` : ''}</p></div>
      <div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" loading={busy === 'status'} onClick={onStatus}><RefreshCw size={14} /> Check status</Button>
        <Button size="sm" variant="secondary" loading={busy === 'recover'} onClick={onRecover}><ArchiveRestore size={14} /> Recover outcome</Button></div></div>
    {receiptNeedsRecovery(receipt) && <p role="alert" className="mt-m text-sm text-warn">The operation may have taken effect. Recover the authoritative receipt before preparing another backup or restore.</p>}
    {receipt.error && <p role="alert" className="mt-m text-sm text-danger">{receipt.error.message}</p>}
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      {receipt.artifact_path && <div><dt className="text-on-surface-low">Artifact</dt><dd className="break-words text-on-surface">{receipt.artifact_path}</dd></div>}
      {receipt.artifact_digest && <div><dt className="text-on-surface-low">Artifact digest</dt><dd className="break-all font-mono text-on-surface">{receipt.artifact_digest}</dd></div>}
      {receipt.source_digest && <div><dt className="text-on-surface-low">Source digest</dt><dd className="break-all font-mono text-on-surface">{receipt.source_digest}</dd></div>}
      {receipt.artifact_bytes != null && <div><dt className="text-on-surface-low">Verified size</dt><dd className="text-on-surface">{bytes(receipt.artifact_bytes)}</dd></div>}
      {receipt.record_count != null && <div><dt className="text-on-surface-low">Records</dt><dd className="text-on-surface">{receipt.record_count.toLocaleString()}</dd></div>}
      {receipt.batch_state && <div><dt className="text-on-surface-low">Restore batch</dt><dd className="text-on-surface">{receipt.batch_state.replaceAll('_', ' ')}</dd></div>}
    </dl>
  </Surface>
}

export function SecurityRecovery() {
  const credentials = useQuery('hypermid:security:credentials', () => api.hypermidSecurityCredentials(), { staleAfterMs: 5_000 })
  const [draft, setDraft] = useState<SecurityRecoveryDraft>(initialSecurityRecoveryDraft)
  const [plan, setPlan] = useState<HypermidSecurityPlanWire>()
  const [planAction, setPlanAction] = useState<SecurityRecoveryAction>()
  const [receipt, setReceipt] = useState<HypermidSecurityReceiptWire>()
  const [reviewed, setReviewed] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [jobId, setJobId] = useState(rememberedSecurityRecoveryJob)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const credential = credentials.data?.credentials.find((item) => item.purposes.includes(draft.action))
  const configured = credential?.configured === true
  const changeDraft = (next: SecurityRecoveryDraft) => {
    setDraft(next); setPlan(undefined); setPlanAction(undefined); setReviewed(false); setConfirmed(false); setError('')
  }
  const acceptReceipt = (next: HypermidSecurityReceiptWire) => {
    setReceipt(next); setJobId(next.job_id); rememberSecurityRecoveryJob(next.job_id)
  }
  const review = async () => {
    const invalid = validateSecurityRecoveryDraft(draft)
    if (invalid) { setError(invalid); return }
    if (!configured) { setError('The owner-bound encrypted backup credential is not configured.'); return }
    setBusy('plan'); setError(''); setPlan(undefined); setReviewed(false); setConfirmed(false)
    try {
      const body = securityRecoveryPlanBody(draft)
      const next = draft.action === 'backup'
        ? await api.planHypermidBackup(body as { destination: string; export_id?: string })
        : await api.planHypermidRestore(body as { artifact_path: string; source_digest: string })
      setPlan(next); setPlanAction(draft.action)
    } catch (caught) { setError(message(caught, 'The encrypted backup plan could not be prepared.')) }
    finally { setBusy('') }
  }
  const apply = async () => {
    if (!plan || !planAction) return
    const decision = securityRecoveryApplyDecision(plan, reviewed ? plan.plan_digest : '', confirmed)
    if (!decision.allowed) { setError(decision.reason); return }
    setBusy('apply'); setError('')
    try {
      acceptReceipt(await api.applyHypermidSecurity(planAction, {
        plan_id: plan.plan_id,
        plan_digest: plan.plan_digest,
        confirm_destructive: confirmed,
      }))
      setPlan(undefined); setPlanAction(undefined); setReviewed(false); setConfirmed(false)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setPlan(undefined); setPlanAction(undefined); setReviewed(false); setConfirmed(false)
        setError('The reviewed security plan is stale or expired. Prepare and review a new plan.')
      } else setError(message(caught, 'The encrypted backup operation was refused.'))
    } finally { setBusy('') }
  }
  const status = async (requested = receipt?.job_id || jobId) => {
    const target = requested.trim()
    if (!target) { setError('Enter a security job ID to recover its receipt.'); return }
    setBusy('status'); setError('')
    try { acceptReceipt(await api.hypermidSecurityStatus(target)) }
    catch (caught) { setError(message(caught, 'The security operation receipt could not be loaded.')) }
    finally { setBusy('') }
  }
  const recover = async () => {
    const target = (receipt?.job_id || jobId).trim()
    if (!target) { setError('Enter a security job ID to recover.'); return }
    setBusy('recover'); setError('')
    try { acceptReceipt(await api.recoverHypermidSecurity(target)) }
    catch (caught) { setError(message(caught, 'The security operation outcome could not be recovered.')) }
    finally { setBusy('') }
  }
  return <div>
    <PanelHeader title="Encrypted backup and recovery" hint="Create and restore encrypted, scope-bound memory exports through reviewed security plans and authoritative receipts." />
    <Section title="Prepare a security plan" hint="The authenticated owner selects the configured credential. Secret material and internal handles never enter this page or request.">
      <RowGroup><Row label="Credential authority" hint="One owner-bound credential is configured for encrypted backup and restore.">
        <div className="flex flex-wrap items-center gap-s"><StatusPill label={configured ? 'configured' : 'not configured'} tone={configured ? 'ok' : 'warn'} />
          <span className="text-sm text-on-surface-low">{credential?.label || 'Local backup key'}</span></div></Row>
        <Row label="Action"><select value={draft.action} onChange={(event) => changeDraft({ ...initialSecurityRecoveryDraft, action: event.target.value as SecurityRecoveryAction })} aria-label="Security recovery action"
          className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
          <option value="backup">Create encrypted backup</option><option value="restore">Restore encrypted backup</option></select></Row>
        {draft.action === 'backup' ? <>
          <Row label="Artifact destination" hint="A protected local path distinct from the active store."><input value={draft.destination} onChange={(event) => changeDraft({ ...draft, destination: event.target.value })} aria-label="Encrypted backup destination" placeholder="/path/to/backup.hmbk"
            className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" /></Row>
          <Row label="Export ID" hint="Optional stable idempotency identity for this backup."><input value={draft.export_id} onChange={(event) => changeDraft({ ...draft, export_id: event.target.value })} aria-label="Backup export ID" placeholder="optional-export-id"
            className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" /></Row>
        </> : <>
          <Row label="Encrypted artifact" hint="The server opens and validates this local artifact before a plan can be reviewed."><input value={draft.artifact_path} onChange={(event) => changeDraft({ ...draft, artifact_path: event.target.value })} aria-label="Encrypted backup artifact" placeholder="/path/to/backup.hmbk"
            className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" /></Row>
          <Row label="Expected source digest" hint="Copy the source digest from the committed backup receipt."><input value={draft.source_digest} onChange={(event) => changeDraft({ ...draft, source_digest: event.target.value.trim().toLowerCase() })} aria-label="Expected backup source digest" placeholder="64 lowercase hexadecimal characters"
            className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" /></Row>
        </>}</RowGroup>
      <div className="mt-m flex justify-end"><Button size="sm" variant="secondary" disabled={!configured} disabledReason={!configured ? 'Configure the security settings before preparing a plan.' : undefined} loading={busy === 'plan'} onClick={() => void review()}>Review security plan</Button></div>
      {plan && <PlanReview plan={plan} reviewed={reviewed} confirmed={confirmed} busy={busy === 'apply'} onReviewed={setReviewed} onConfirmed={setConfirmed} onApply={() => void apply()} />}
    </Section>
    <Section title="Outcome recovery" hint="Load the authoritative job receipt. An unknown outcome must be recovered before retrying.">
      <div className="flex flex-wrap gap-s"><input value={jobId} onChange={(event) => setJobId(event.target.value)} aria-label="Security operation job ID" placeholder="job-…"
        className="min-h-11 min-w-0 flex-1 rounded-lg border border-outline-variant bg-surface px-m font-mono text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" />
        <Button size="sm" variant="secondary" loading={busy === 'status'} onClick={() => void status()}>Load receipt</Button></div>
      {receipt && <Receipt receipt={receipt} busy={busy} onStatus={() => void status(receipt.job_id)} onRecover={() => void recover()} />}
    </Section>
    {Boolean(credentials.error) && <p role="alert" className="mt-m flex items-start gap-s text-sm text-danger"><TriangleAlert size={16} className="mt-0.5 shrink-0" />The credential authority could not be loaded.</p>}
    {error && <p role="alert" className="mt-m flex items-start gap-s text-sm text-danger"><TriangleAlert size={16} className="mt-0.5 shrink-0" />{error}</p>}
  </div>
}
