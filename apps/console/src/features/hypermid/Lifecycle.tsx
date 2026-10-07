import { TextInput, Select, Checkbox } from '../../shared/ui/forms'
import { useEffect, useState } from 'react'
import { ArchiveRestore, FileCheck2, RefreshCw, RotateCcw, ShieldCheck, TriangleAlert } from 'lucide-react'
import { api, ApiError, type HypermidLifecycleAction } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { PanelHeader, Row, RowGroup, Section } from '../settings/settingsUI'
import {
  LIFECYCLE_ACTIONS,
  decideLifecycleApply,
  lifecycleInstallEvidence,
  lifecyclePlanBody,
  recoveryAction,
  rememberedLifecycleJob,
  rememberLifecycleJob,
  validateLifecycleDraft,
  type DataDisposition,
  type LifecycleDraft,
  type LifecyclePlan,
  type LifecycleReceipt,
  type LifecycleRecoveryState,
} from './lifecycleState'

function message(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

function tone(state: string): 'ok' | 'warn' | 'muted' {
  if (state === 'committed') return 'ok'
  if (['running', 'resumable', 'rollback_available', 'outcome_unknown', 'failed'].includes(state)) return 'warn'
  return 'muted'
}

function bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

function LifecycleFields({ draft, onChange }: { draft: LifecycleDraft; onChange: (next: LifecycleDraft) => void }) {
  if (draft.action === 'install' || draft.action === 'update') return <Row label="Target release" hint="The daemon validates platform, compatibility, space, restart, migration, and rollback requirements.">
    <TextInput value={draft.target_version} onChange={(value) => onChange({ ...draft, target_version: value })} ariaLabel="Target release" className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" placeholder="1.2.3" />
  </Row>
  if (draft.action === 'uninstall') return <Row label="User data" hint="Runtime wiring and user data remain separate. Purge requires a second confirmation after plan review.">
    <Select value={draft.data_disposition} onChange={(value) => onChange({ ...draft, data_disposition: value as DataDisposition })} ariaLabel="Uninstall data disposition" className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" options={[{ value: "retain", label: "Retain user data" }, { value: "export", label: "Export before uninstall" }, { value: "purge", label: "Permanently purge user data" }]} />
  </Row>
  if (draft.action === 'migrate' || draft.action === 'restore') return <Row label="Verified source" hint="The daemon stages and verifies this source before any visible state changes.">
    <TextInput value={draft.source} onChange={(value) => onChange({ ...draft, source: value })} ariaLabel="Lifecycle source" className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" placeholder="/path/to/artifact" />
  </Row>
  if (draft.action === 'export') return <Row label="Export destination" hint="The verified export excludes credentials, local authentication material, and rebuildable derivatives.">
    <TextInput value={draft.destination} onChange={(value) => onChange({ ...draft, destination: value })} ariaLabel="Export destination" className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" placeholder="/path/to/export" />
  </Row>
  return <Row label="Rollback source" hint="The daemon selects only previously verified rollback material.">
    <span className="text-sm text-on-surface-low">No local override. Review the daemon inventory and digest before apply.</span>
  </Row>
}

function PlanReview({ plan, reviewed, destructiveConfirmed, purgeConfirmed, busy, onReviewed, onDestructive, onPurge, onApply }: {
  plan: LifecyclePlan
  reviewed: boolean
  destructiveConfirmed: boolean
  purgeConfirmed: boolean
  busy: boolean
  onReviewed: (value: boolean) => void
  onDestructive: (value: boolean) => void
  onPurge: (value: boolean) => void
  onApply: () => void
}) {
  const decision = decideLifecycleApply(plan, reviewed ? plan.plan_digest : '', destructiveConfirmed, purgeConfirmed)
  const inventory = Object.entries(plan.inventory || {})
  const install = lifecycleInstallEvidence(plan)
  return <section aria-labelledby="lifecycle-plan-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><h3 id="lifecycle-plan-title" className="text-base text-on-surface">Review lifecycle plan</h3>
      <p className="mt-xs text-sm text-on-surface-low">Expires {new Date(plan.expires_at).toLocaleString()}</p></div><div className="flex flex-wrap gap-s">
      {plan.restart_required && <StatusPill label="restart required" tone="warn" />}{plan.destructive && <StatusPill label="destructive" tone="warn" />}
      {plan.data_disposition && <StatusPill label={`data: ${plan.data_disposition}`} tone={plan.data_disposition === 'purge' ? 'warn' : 'muted'} />}</div></div>
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Owner scope</dt><dd className="break-words font-mono text-on-surface">{plan.scope.owner_id}</dd></div>
      <div><dt className="text-on-surface-low">Project scope</dt><dd className="break-words font-mono text-on-surface">{plan.scope.project_id}</dd></div>
      {plan.scope.workspace_id && <div><dt className="text-on-surface-low">Workspace scope</dt><dd className="break-words font-mono text-on-surface">{plan.scope.workspace_id}</dd></div>}
      {plan.current_version && <div><dt className="text-on-surface-low">Current version</dt><dd className="text-on-surface">{plan.current_version}</dd></div>}
      {plan.target_version && <div><dt className="text-on-surface-low">Target version</dt><dd className="text-on-surface">{plan.target_version}</dd></div>}
      {install.binaryPath && <div><dt className="text-on-surface-low">Packaged binary</dt><dd className="break-words font-mono text-on-surface">{install.binaryPath}</dd></div>}
      {install.operator && <div><dt className="text-on-surface-low">OS operator</dt><dd className="text-on-surface">{install.operator}</dd></div>}
      {install.packagedBinaryVerified != null && <div><dt className="text-on-surface-low">Packaged binary check</dt><dd><StatusPill label={install.packagedBinaryVerified ? 'verified' : 'not verified'} tone={install.packagedBinaryVerified ? 'ok' : 'warn'} /></dd></div>}
      {plan.destination && <div><dt className="text-on-surface-low">Destination</dt><dd className="break-words text-on-surface">{plan.destination}</dd></div>}
      {plan.staging_id && <div><dt className="text-on-surface-low">Staging record</dt><dd className="break-words text-on-surface">{plan.staging_id}</dd></div>}
      {plan.resume_after && <div><dt className="text-on-surface-low">Resume after</dt><dd className="text-on-surface">{plan.resume_after.epoch}:{plan.resume_after.sequence}</dd></div>}
      {plan.estimated_items != null && <div><dt className="text-on-surface-low">Estimated items</dt><dd className="text-on-surface">{plan.estimated_items.toLocaleString()}</dd></div>}
      {plan.estimated_bytes != null && <div><dt className="text-on-surface-low">Estimated data</dt><dd className="text-on-surface">{bytes(plan.estimated_bytes)}</dd></div>}
    </dl>
    {plan.source_digest && <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Source digest {plan.source_digest}</p>}
    {install.binaryDigest && <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Packaged binary digest {install.binaryDigest}</p>}
    {plan.rollback_digest && <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Rollback digest {plan.rollback_digest}</p>}
    {plan.authority_digest && <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Authority digest {plan.authority_digest}</p>}
    {plan.blocker_digest && <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Blocker digest {plan.blocker_digest}</p>}
    {install.expiresMs != null && <Surface tone="container" radius="lg" className="mt-m p-m"><h4 className="text-sm text-on-surface">Local enrollment</h4>
      <dl className="mt-s grid gap-s text-sm sm:grid-cols-2">
        <div><dt className="text-on-surface-low">Operations</dt><dd className="break-words font-mono text-on-surface">{install.operations.join(', ') || 'None'}</dd></div>
        <div><dt className="text-on-surface-low">Resources</dt><dd className="break-words font-mono text-on-surface">{install.resources.join(', ') || 'None'}</dd></div>
        <div><dt className="text-on-surface-low">Expires</dt><dd className="text-on-surface">{install.expiresMs} · {new Date(install.expiresMs).toLocaleString()}</dd></div>
      </dl>
    </Surface>}
    {inventory.length > 0 && <div className="mt-m"><h4 className="text-sm text-on-surface">Inventory</h4><div className="mt-s grid gap-s sm:grid-cols-2">
      {inventory.map(([kind, entries]) => <Surface key={kind} tone="container" radius="lg" className="p-m"><p className="text-sm text-on-surface">{kind.replaceAll('_', ' ')}</p>
        <p data-type="caption" className="mt-xs break-words text-on-surface-low">{entries.join(', ') || 'None'}</p></Surface>)}</div></div>}
    {install.currentEnrollmentDigest && <Surface tone="container" radius="lg" className="mt-m p-m">
      <h4 className="text-sm text-on-surface">Review runtime permissions</h4>
      <p className="mt-xs text-sm text-on-surface-low">Applying this plan restarts the local runtime and preserves its identity and saved memory.</p>
      <p className="mt-s text-sm text-on-surface">New operations: {install.addedOperations.join(', ') || 'None'}</p>
      <p className="mt-xs text-sm text-on-surface">New resources: {install.addedResources.join(', ') || 'None'}</p>
      {install.addedOperations.includes('administer') && <p className="mt-s text-sm text-on-surface-low">Memory service administration enables app memory activation and private work lifetimes.</p>}
      {install.addedResources.includes('memory-embedding') && <p className="mt-xs text-sm text-on-surface-low">Embedding access enables semantic memory lookup and rebuilding its derived index.</p>}
      <p data-type="caption" className="mt-s break-all font-mono text-on-surface-low">Current enrollment digest {install.currentEnrollmentDigest}</p>
    </Surface>}
    {plan.exclusions && plan.exclusions.length > 0 && <p className="mt-m text-sm text-on-surface-low">Excluded from artifact: {plan.exclusions.join(', ')}.</p>}
    <ol className="mt-m grid gap-s">{plan.steps.map((step, index) => <li key={step.id} className="rounded-lg bg-surface-container p-m">
      <div className="flex flex-wrap items-center gap-s"><span className="text-xs text-on-surface-low">{index + 1}</span><span className="text-sm text-on-surface">{step.title}</span>
        <StatusPill label={step.effect.replaceAll('_', ' ')} tone={step.effect === 'read' ? 'muted' : 'warn'} /></div>{step.detail && <p className="mt-xs text-sm text-on-surface-low">{step.detail}</p>}
    </li>)}</ol>
    {plan.blockers.length > 0 && <div role="alert" className="mt-m rounded-lg border border-danger/40 bg-danger/10 p-m"><p className="text-sm text-on-surface">This plan cannot be applied:</p>
      <ul className="mt-s list-disc pl-l text-sm text-on-surface-low">{plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
    <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Plan digest {plan.plan_digest}</p>
    <label className="hypermid-touch mt-m flex cursor-pointer items-center gap-s text-sm text-on-surface"><Checkbox checked={reviewed} onChange={(checked) => onReviewed(checked)} className="size-4 accent-primary" ariaLabel={"I reviewed this exact scope, inventory, plan, and digest."} />I reviewed this exact scope, inventory, plan, and digest.</label>
    {plan.destructive && <label className="hypermid-touch mt-s flex cursor-pointer items-center gap-s text-sm text-on-surface"><Checkbox checked={destructiveConfirmed} onChange={(checked) => onDestructive(checked)} className="size-4 accent-primary" ariaLabel={"I confirm the listed destructive effects."} />I confirm the listed destructive effects.</label>}
    {plan.data_disposition === 'purge' && <label className="hypermid-touch mt-s flex cursor-pointer items-center gap-s text-sm text-on-surface"><Checkbox checked={purgeConfirmed} onChange={(checked) => onPurge(checked)} className="size-4 accent-primary" ariaLabel={"I separately confirm permanent user-data purge."} />I separately confirm permanent user-data purge.</label>}
    {!decision.allowed && <p className="mt-s text-sm text-on-surface-low">{decision.reason}</p>}
    <div className="hypermid-action-bar mt-m flex justify-end"><Button size="sm" disabled={!decision.allowed} disabledReason={!decision.allowed ? decision.reason : undefined} loading={busy} onClick={onApply}><ShieldCheck size={14} /> Apply reviewed plan</Button></div>
  </section>
}

function RecoveryReceipt({ receipt, recoveryState, busy, onCheck, onRecover, onResume, onRollback }: {
  receipt: LifecycleReceipt
  recoveryState?: LifecycleRecoveryState
  busy: string
  onCheck: () => void
  onRecover: () => void
  onResume: () => void
  onRollback: () => void
}) {
  const action = recoveryState ? recoveryAction(recoveryState) : 'none'
  const error = receipt.error?.message
  return <div aria-live={receipt.state === 'running' ? undefined : 'polite'}><Surface tone="container" radius="lg" className="mt-m p-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><div className="flex flex-wrap items-center gap-s"><FileCheck2 size={16} className="text-primary" />
      <h3 className="text-sm text-on-surface">Lifecycle receipt</h3><StatusPill label={receipt.state.replaceAll('_', ' ')} tone={tone(receipt.state)} />
      {recoveryState && <StatusPill label={`recovery: ${recoveryState.replaceAll('_', ' ')}`} tone={tone(recoveryState)} />}</div>
      <p data-type="caption" className="mt-xs text-on-surface-low">Job {receipt.job_id}{receipt.cursor ? ` · cursor ${receipt.cursor.epoch}:${receipt.cursor.sequence}` : ''}</p></div>
      <div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" loading={busy === 'status'} onClick={onCheck}><RefreshCw size={14} /> Check status</Button>
        <Button size="sm" variant="secondary" loading={busy === 'recover'} onClick={onRecover}><ArchiveRestore size={14} /> Recover</Button></div></div>
    <div className="mt-m grid gap-s">{receipt.steps.map((step) => <div key={step.id} className="flex flex-wrap items-center justify-between gap-s rounded-lg bg-surface px-m py-s text-sm">
      <span className="text-on-surface">{step.title}</span><StatusPill label={step.state} tone={tone(step.state)} /></div>)}</div>
    {(receipt.state === 'outcome_unknown' || recoveryState === 'outcome_unknown') && <p role="alert" className="mt-m text-sm text-warn">The operation may have taken effect. Recover authoritative state before retrying or preparing another mutation.</p>}
    {typeof error === 'string' && <p role="alert" className="mt-m text-sm text-danger">{error}</p>}
    {receipt.artifact_digest && <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Artifact digest {receipt.artifact_digest}</p>}
    {receipt.artifact_bytes != null && <p data-type="caption" className="mt-xs text-on-surface-low">Verified artifact size {bytes(receipt.artifact_bytes)}{receipt.artifact_path ? ` · ${receipt.artifact_path}` : ''}</p>}
    {action === 'resume' && <div className="mt-m flex justify-end"><Button size="sm" loading={busy === 'resume'} onClick={onResume}>Resume after reported cursor</Button></div>}
    {action === 'rollback' && <div className="mt-m flex justify-end"><Button size="sm" variant="secondary" onClick={onRollback}><RotateCcw size={14} /> Prepare rollback plan</Button></div>}
    {action === 'recheck' && <p className="mt-m text-sm text-on-surface-low">Run recovery again after the daemon reconciles its journal.</p>}
  </Surface></div>
}

const initialDraft: LifecycleDraft = { action: 'update', target_version: '', data_disposition: 'retain', source: '', destination: '' }

export function Lifecycle() {
  const [draft, setDraft] = useState(initialDraft)
  const [plan, setPlan] = useState<LifecyclePlan>()
  const [planAction, setPlanAction] = useState<HypermidLifecycleAction>()
  const [receipt, setReceipt] = useState<LifecycleReceipt>()
  const [recoveryState, setRecoveryState] = useState<LifecycleRecoveryState>()
  const [reviewed, setReviewed] = useState(false)
  const [destructiveConfirmed, setDestructiveConfirmed] = useState(false)
  const [purgeConfirmed, setPurgeConfirmed] = useState(false)
  const [jobId, setJobId] = useState(rememberedLifecycleJob)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const acceptReceipt = (next: LifecycleReceipt) => {
    setReceipt(next); setJobId(next.job_id); rememberLifecycleJob(next.job_id)
  }
  const changeDraft = (next: LifecycleDraft) => {
    setDraft(next); setPlan(undefined); setPlanAction(undefined); setReviewed(false); setDestructiveConfirmed(false); setPurgeConfirmed(false)
  }
  const review = async () => {
    const invalid = validateLifecycleDraft(draft)
    if (invalid) { setError(invalid); return }
    setBusy('plan'); setError(''); setPlan(undefined); setRecoveryState(undefined)
    try { setPlan(await api.planHypermidLifecycle(draft.action, lifecyclePlanBody(draft)) as LifecyclePlan); setPlanAction(draft.action) }
    catch (caught) { setError(message(caught, 'The lifecycle plan could not be prepared.')) }
    finally { setBusy('') }
  }
  const apply = async () => {
    if (!plan || !planAction) return
    const decision = decideLifecycleApply(plan, reviewed ? plan.plan_digest : '', destructiveConfirmed, purgeConfirmed)
    if (!decision.allowed) { setError(decision.reason); return }
    setBusy('apply'); setError('')
    try {
      acceptReceipt(await api.applyHypermidLifecycle(planAction, {
        plan_id: plan.plan_id,
        plan_digest: plan.plan_digest,
        confirm_destructive: destructiveConfirmed,
        confirm_purge: purgeConfirmed,
      }))
      setPlan(undefined); setPlanAction(undefined)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setPlan(undefined); setPlanAction(undefined); setReviewed(false)
        setError('The reviewed lifecycle plan is stale or expired. Prepare and review a new plan.')
      } else setError(message(caught, 'The lifecycle operation was refused.'))
    } finally { setBusy('') }
  }
  const status = async (requestedJob = jobId) => {
    const target = requestedJob.trim()
    if (!target) { setError('Enter a lifecycle job ID to recover its receipt.'); return }
    setBusy('status'); setError('')
    try { acceptReceipt(await api.hypermidLifecycleStatus(target)) }
    catch (caught) { setError(message(caught, 'The lifecycle receipt could not be loaded.')) }
    finally { setBusy('') }
  }
  const recover = async () => {
    const target = (receipt?.job_id || jobId).trim()
    if (!target) { setError('Enter a lifecycle job ID to recover.'); return }
    setBusy('recover'); setError('')
    try {
      const recovered = await api.recoverHypermidLifecycle(target)
      acceptReceipt(recovered.receipt)
      setRecoveryState(recovered.recovery_state as LifecycleRecoveryState)
    } catch (caught) { setError(message(caught, 'Lifecycle recovery failed.')) }
    finally { setBusy('') }
  }
  const resume = async () => {
    if (!receipt) return
    setBusy('resume'); setError('')
    try { acceptReceipt(await api.resumeHypermidLifecycle(receipt.job_id, receipt.cursor || undefined)); setRecoveryState(undefined) }
    catch (caught) { setError(message(caught, 'Lifecycle resume was refused.')) }
    finally { setBusy('') }
  }
  const prepareRollback = () => {
    changeDraft({ ...initialDraft, action: 'rollback' })
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  useEffect(() => {
    if (!receipt || receipt.state !== 'running') return
    const timer = window.setInterval(() => { void status(receipt.job_id) }, 2_000)
    return () => window.clearInterval(timer)
  }, [receipt?.job_id, receipt?.state])

  const selected = LIFECYCLE_ACTIONS.find((item) => item.id === draft.action)!
  return <div><PanelHeader title="Lifecycle" hint="Install, update, move, export, restore, or remove Hypermid through daemon-authored plans and recoverable receipts." />
    <Section title="Prepare a lifecycle plan" hint="Inputs select intent only. The authenticated daemon determines scope, inventory, blockers, staging, and exact effects.">
      <RowGroup><Row label="Action" hint={selected.detail}><Select value={draft.action} onChange={(value) => changeDraft({ ...initialDraft, action: value as HypermidLifecycleAction })} ariaLabel="Lifecycle action" className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" options={[...LIFECYCLE_ACTIONS.map(item => ({ value: item.id, label: item.label }))]} /></Row>
        <LifecycleFields draft={draft} onChange={changeDraft} /></RowGroup>
      <div className="mt-m flex justify-end"><Button size="sm" variant="secondary" loading={busy === 'plan'} onClick={() => void review()}>Review lifecycle plan</Button></div>
      {plan && <PlanReview plan={plan} reviewed={reviewed} destructiveConfirmed={destructiveConfirmed} purgeConfirmed={purgeConfirmed} busy={busy === 'apply'}
        onReviewed={setReviewed} onDestructive={setDestructiveConfirmed} onPurge={setPurgeConfirmed} onApply={() => void apply()} />}
    </Section>
    <Section title="Receipt recovery" hint="Reconnect using a job ID, then recover daemon journal state before deciding to resume or roll back.">
      <div className="flex flex-wrap gap-s"><TextInput value={jobId} onChange={(value) => setJobId(value)} ariaLabel="Lifecycle job ID" className="min-h-11 min-w-0 flex-1 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" />
        <Button size="sm" variant="secondary" loading={busy === 'status'} onClick={() => void status()}>Load receipt</Button>
        <Button size="sm" variant="secondary" loading={busy === 'recover'} onClick={() => void recover()}>Recover state</Button></div>
      {receipt && <RecoveryReceipt receipt={receipt} recoveryState={recoveryState} busy={busy} onCheck={() => void status(receipt.job_id)} onRecover={() => void recover()}
        onResume={() => void resume()} onRollback={prepareRollback} />}
      {error && <p role="alert" className="mt-m flex items-start gap-s text-sm text-danger"><TriangleAlert size={16} className="mt-0.5 shrink-0" />{error}</p>}
    </Section>
  </div>
}
