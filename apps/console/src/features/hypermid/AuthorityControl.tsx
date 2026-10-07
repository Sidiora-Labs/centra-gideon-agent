import { Checkbox } from '../../shared/ui/forms'
import { useEffect, useState } from 'react'
import { RefreshCw, ShieldCheck, Undo2 } from 'lucide-react'
import { api, ApiError, type HypermidAuthorityPlanWire, type HypermidAuthorityStatusWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'
import { authorityPlanDecision } from './authorityState'

function humanStep(value: string): string {
  const label = value.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

export function AuthorityControl({ onStatus, onCommitted }: {
  onStatus?: (status: HypermidAuthorityStatusWire) => void
  onCommitted?: () => void
}) {
  const statusQuery = useQuery('hypermid:authority:status', () => api.hypermidAuthorityStatus(), { staleAfterMs: 5_000 })
  const [status, setStatus] = useState<HypermidAuthorityStatusWire>()
  const [plan, setPlan] = useState<HypermidAuthorityPlanWire>()
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [outcome, setOutcome] = useState('')
  useEffect(() => { if (statusQuery.data) setStatus(statusQuery.data) }, [statusQuery.data])
  useEffect(() => { if (status) onStatus?.(status) }, [onStatus, status])
  if (!status && statusQuery.error) return <LoadError what="Hypermid writer authority" error={statusQuery.error} onRetry={statusQuery.refresh} />
  if (!status) return <FormSkeleton sections={1} rows={2} what="Hypermid writer authority" />

  const active = status.writer === 'hypermid' && status.owns_writes
  const action = active ? 'rollback_to_gideon' as const : 'activate_primary' as const
  const canPlan = active || status.mode === 'primary'
  const prepare = async () => {
    setBusy('plan'); setError(''); setOutcome(''); setReviewed(false); setPlan(undefined)
    try { setPlan(await api.planHypermidAuthority(action)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The authority plan could not be prepared.') }
    finally { setBusy('') }
  }
  const apply = async () => {
    if (!plan) return
    const decision = authorityPlanDecision(plan, reviewed ? plan.plan_digest : '')
    if (!decision.allowed) { setError(decision.reason); return }
    setBusy('apply'); setError('')
    try {
      const receipt = await api.applyHypermidAuthority(plan)
      setStatus(receipt.status); setPlan(undefined); setReviewed(false)
      setOutcome(receipt.state)
      if (receipt.state === 'committed') onCommitted?.()
      if (receipt.error) setError(receipt.error.message)
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setPlan(undefined); setReviewed(false); setError('Writer authority changed or the plan expired. Prepare and review a new plan.')
      } else setError(caught instanceof Error ? caught.message : 'The authority handoff was refused.')
    } finally { setBusy('') }
  }
  const reconcile = async () => {
    setBusy('reconcile'); setError('')
    try { setStatus(await api.hypermidAuthorityStatus(true)); setOutcome('') }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Writer authority could not be reconciled.') }
    finally { setBusy('') }
  }
  const decision = plan ? authorityPlanDecision(plan, reviewed ? plan.plan_digest : '') : undefined
  return <Section title="Writer authority" hint="Primary activation and rollback use a short-lived reviewed plan; only one scoped writer can be active.">
    <Surface tone="container" radius="lg" className="p-l">
      <div className="flex flex-wrap items-center gap-s"><StatusPill label={active ? 'Hypermid writes' : 'Gideon writes'} tone={active ? 'ok' : 'muted'} />
        <StatusPill label={status.lease_state.replaceAll('_', ' ')} tone={status.lease_state === 'held' ? 'ok' : status.lease_state === 'unknown' ? 'warn' : 'muted'} />
        <span data-type="caption" className="text-on-surface-low">Mode {status.mode.replaceAll('_', ' ')} · authority generation {status.authority_epoch}</span></div>
      {status.failure && <p role="alert" data-type="body-s" className="mt-s break-words text-danger">{status.failure}</p>}
      {!canPlan && <p data-type="body-s" className="mt-s text-on-surface-low">Stage Primary mode and wait for the safe turn boundary before preparing the writer handoff.</p>}
      <div className="mt-m flex flex-wrap justify-end gap-s">
        {status.lease_state === 'unknown' && <Button size="sm" variant="secondary" loading={busy === 'reconcile'} onClick={() => void reconcile()}><RefreshCw size={14} /> Reconcile authority</Button>}
        <Button size="sm" variant={active ? 'danger' : 'secondary'} disabled={!canPlan || status.lease_state === 'unknown'} disabledReason={busy === 'plan' ? 'Preparing the authority review.' : status.lease_state === 'unknown' ? 'Reconcile writer authority before reviewing a handoff.' : !canPlan ? 'Stage Primary mode and wait for the safe turn boundary.' : undefined} loading={busy === 'plan'} onClick={() => void prepare()}>
          {active ? <Undo2 size={14} /> : <ShieldCheck size={14} />}{active ? 'Review rollback to Gideon' : 'Review Primary handoff'}
        </Button>
      </div>
    </Surface>
    {plan && <section aria-labelledby="authority-plan-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
      <h3 id="authority-plan-title" data-type="title-m" className="text-on-surface">{plan.action === 'activate_primary' ? 'Review Primary writer handoff' : 'Review rollback to Gideon'}</h3>
      <p data-type="caption" className="mt-xs text-on-surface-low">This plan expires {new Date(plan.expires_at).toLocaleString()}.</p>
      <ol className="mt-m grid gap-s">{plan.steps.map((step, index) => <li key={step} data-type="body-s" className="rounded-lg bg-surface-container px-m py-s text-on-surface">{index + 1}. {humanStep(step)}</li>)}</ol>
      {plan.blockers.length > 0 && <div role="alert" data-type="body-s" className="mt-m rounded-lg bg-danger/10 p-m text-danger"><p>Handoff is blocked:</p><ul className="mt-s list-disc pl-l">{plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul></div>}
      <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Plan digest {plan.plan_digest}</p>
      <label data-type="label-s" className="hypermid-touch mt-m flex cursor-pointer items-center gap-s text-on-surface"><Checkbox checked={reviewed} onChange={(checked) => setReviewed(checked)} className="size-4 accent-primary" ariaLabel={"I reviewed this exact scope, sequence, and digest."} />I reviewed this exact scope, sequence, and digest.</label>
      {!decision?.allowed && <p data-type="body-s" className="mt-s text-on-surface-low">{decision?.reason}</p>}
      <div className="hypermid-action-bar mt-m flex flex-wrap justify-end gap-s"><Button size="sm" variant="secondary" onClick={() => { setPlan(undefined); setReviewed(false) }}>Cancel</Button>
        <Button size="sm" disabled={!decision?.allowed} disabledReason={!decision?.allowed ? decision?.reason : undefined} loading={busy === 'apply'} onClick={() => void apply()}>Apply reviewed handoff</Button></div>
    </section>}
    {outcome === 'outcome_unknown' && <p role="alert" data-type="body-s" className="mt-m text-warn">The handoff may have taken effect. Reconcile writer authority before retrying.</p>}
    {outcome === 'committed' && <p role="status" data-type="body-s" className="mt-m text-success">Writer authority changed and the returned status confirms the result.</p>}
    {error && <p role="alert" data-type="body-s" className="mt-m break-words text-danger">{error}</p>}
  </Section>
}
