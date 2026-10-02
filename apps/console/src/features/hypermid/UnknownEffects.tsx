import { useState } from 'react'
import { Clock3, FileCheck2, RefreshCw, ShieldQuestion } from 'lucide-react'
import {
  api,
  type HypermidEffectReviewPlanWire,
  type HypermidEffectSnapshotWire,
  type HypermidEffectsWire,
} from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'

function readable(value: string): string {
  return value.replaceAll('_', ' ').replace(/^./, (character) => character.toUpperCase())
}

function tone(state: string): 'ok' | 'warn' | 'muted' {
  if (state === 'committed' || state === 'not_started') return 'ok'
  if (state === 'unknown') return 'warn'
  return 'muted'
}

function EffectCard({ effect, busy, onReview }: {
  effect: HypermidEffectSnapshotWire
  busy: boolean
  onReview: (effect: HypermidEffectSnapshotWire) => void
}) {
  const awaitingProof = effect.state === 'unknown' && !effect.reviewable && !effect.review_plan
  return <Surface tone="container" radius="lg" className="min-w-0 p-l">
    <div className="flex flex-wrap items-start justify-between gap-m">
      <div className="flex min-w-0 items-start gap-s"><ShieldQuestion size={17} aria-hidden className="mt-0.5 shrink-0 text-primary" />
        <div><h3 className="break-words text-sm font-medium text-on-surface">{readable(effect.operation)}</h3>
          <p data-type="caption" className="mt-xs text-on-surface-low">Recorded {new Date(effect.created_ms).toLocaleString()} · {effect.scope.workspace_id ? 'current workspace' : 'current project'}</p></div></div>
      <StatusPill label={readable(effect.state)} tone={tone(effect.state)} />
    </div>
    <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Input digest {effect.input_digest}</p>
    {effect.reason && <p className="mt-s break-words text-sm text-on-surface-low">{effect.reason}</p>}
    {effect.next_action === 'wait_for_recovery' && <p className="mt-s flex items-start gap-s text-sm text-on-surface-low"><Clock3 size={15} aria-hidden className="mt-0.5 shrink-0" />Wait for daemon recovery before taking another action.</p>}
    {awaitingProof && <p className="mt-s text-sm text-on-surface-low">Await authoritative provider status. Reconciliation remains unavailable until the daemon binds one unambiguous proof.</p>}
    {(effect.reviewable || effect.review_plan) && <div className="mt-m flex justify-end"><Button size="sm" variant="secondary" className="hypermid-touch" loading={busy}
      onClick={() => onReview(effect)}>{effect.review_plan ? 'Review saved reconciliation' : 'Check authoritative status'}</Button></div>}
  </Surface>
}

function PlanReview({ plan, reviewed, busy, onReviewed, onCancel, onReconcile }: {
  plan: HypermidEffectReviewPlanWire
  reviewed: boolean
  busy: boolean
  onReviewed: (value: boolean) => void
  onCancel: () => void
  onReconcile: () => void
}) {
  return <div role="dialog" aria-modal="false" aria-labelledby="effect-review-title" className="hypermid-review mt-m rounded-lg border border-outline-variant bg-surface p-l">
    <div className="flex flex-wrap items-start justify-between gap-s"><div><h3 id="effect-review-title" className="text-base text-on-surface">Review authoritative reconciliation</h3>
      <p className="mt-xs text-sm text-on-surface-low">This records the provider-confirmed outcome. It does not repeat the external effect.</p></div>
      <StatusPill label={readable(plan.proposed_state)} tone="ok" /></div>
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Operation</dt><dd className="break-words text-on-surface">{readable(plan.operation)}</dd></div>
      <div><dt className="text-on-surface-low">Scope</dt><dd className="text-on-surface">{plan.scope.workspace_id ? 'Current workspace' : 'Current project'}</dd></div>
      <div><dt className="text-on-surface-low">Proof observed</dt><dd className="text-on-surface">{new Date(plan.created_ms).toLocaleString()}</dd></div>
      <div><dt className="text-on-surface-low">Outcome evidence</dt><dd className="break-words text-on-surface">{plan.result_digest ? 'Committed result digest available' : plan.reason || 'Authoritative not-started proof'}</dd></div>
    </dl>
    <p data-type="caption" className="mt-m break-all font-mono text-on-surface-low">Input digest {plan.input_digest}</p>
    <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Provider proof digest {plan.provider_proof_digest}</p>
    {plan.result_digest && <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Result digest {plan.result_digest}</p>}
    <p data-type="caption" className="mt-xs break-all font-mono text-on-surface-low">Plan digest {plan.plan_digest}</p>
    <label className="hypermid-touch mt-m flex cursor-pointer items-center gap-s text-sm text-on-surface"><input type="checkbox" checked={reviewed} onChange={(event) => onReviewed(event.target.checked)} className="size-4 accent-primary" />I reviewed this exact operation, scope, proposed outcome, and evidence digest.</label>
    <div className="hypermid-action-bar mt-m flex flex-wrap justify-end gap-s"><Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button>
      <Button size="sm" disabled={!reviewed} loading={busy} onClick={onReconcile}><FileCheck2 size={14} /> Record reviewed outcome</Button></div>
  </div>
}

export function UnknownEffectsView({ snapshot, plan, reviewed = false, busy = '', error = '', announcement = '', onRefresh, onReview, onReviewed, onCancel, onReconcile }: {
  snapshot: HypermidEffectsWire
  plan?: HypermidEffectReviewPlanWire
  reviewed?: boolean
  busy?: string
  error?: string
  announcement?: string
  onRefresh: () => void
  onReview: (effect: HypermidEffectSnapshotWire) => void
  onReviewed: (value: boolean) => void
  onCancel: () => void
  onReconcile: () => void
}) {
  return <Section title="Unsettled external effects" hint="Ambiguous MCP or stdio mutations stay blocked until the daemon binds an authoritative provider proof. Gideon never repeats them automatically."
    right={<Button size="sm" variant="secondary" className="hypermid-touch" onClick={onRefresh}><RefreshCw size={14} /> Refresh</Button>}>
    {snapshot.effects.length === 0 ? <EmptyState title="No unsettled effects" hint="The authenticated scope has no external mutations awaiting recovery or reconciliation." />
      : <div className="grid gap-m lg:grid-cols-2">{snapshot.effects.map((effect) => <EffectCard key={effect.effect_id} effect={effect} busy={busy === effect.effect_id} onReview={onReview} />)}</div>}
    {plan && <PlanReview plan={plan} reviewed={reviewed} busy={busy === 'reconcile'} onReviewed={onReviewed} onCancel={onCancel} onReconcile={onReconcile} />}
    {error && <p role="alert" aria-live="assertive" className="mt-m break-words text-sm text-danger">{error}</p>}
    <p role="status" aria-live="polite" className="sr-only">{announcement}</p>
    <p data-type="caption" className="mt-m text-on-surface-low">Checked {new Date(snapshot.checked_at_ms).toLocaleString()}.</p>
  </Section>
}

export function UnknownEffects() {
  const query = useQuery('hypermid:effects', () => api.hypermidEffects(), { staleAfterMs: 5_000 })
  const [plan, setPlan] = useState<HypermidEffectReviewPlanWire>()
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [announcement, setAnnouncement] = useState('')
  if (!query.data && query.error) return <Section title="Unsettled external effects"><Surface tone="container" radius="lg" className="p-l">
    <p className="text-sm text-on-surface">Effect status is unavailable.</p><p data-type="caption" className="mt-xs text-on-surface-low">Refresh after the authenticated daemon connection recovers.</p>
    <Button size="sm" variant="secondary" className="hypermid-touch mt-s" onClick={query.refresh}>Refresh status</Button>
  </Surface></Section>
  if (!query.data) return <FormSkeleton sections={1} rows={3} what="Hypermid external effects" />

  const review = async (effect: HypermidEffectSnapshotWire) => {
    setError(''); setReviewed(false)
    if (effect.review_plan) { setPlan(effect.review_plan); return }
    setBusy(effect.effect_id)
    try { setPlan((await api.reviewHypermidEffect(effect.effect_id)).plan) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Authoritative effect evidence is unavailable.') }
    finally { setBusy('') }
  }
  const reconcile = async () => {
    if (!plan || !reviewed) return
    setBusy('reconcile'); setError(''); setAnnouncement('')
    try {
      const result = await api.reconcileHypermidEffect(plan.effect_id, plan.review_id, plan.plan_digest)
      setAnnouncement(`Effect reconciliation recorded as ${readable(result.state)}.`)
      setPlan(undefined); setReviewed(false); query.refresh()
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Effect reconciliation was refused.') }
    finally { setBusy('') }
  }
  return <UnknownEffectsView snapshot={query.data} plan={plan} reviewed={reviewed} busy={busy} error={error} announcement={announcement}
    onRefresh={query.refresh} onReview={(effect) => void review(effect)} onReviewed={setReviewed} onCancel={() => { setPlan(undefined); setReviewed(false) }} onReconcile={() => void reconcile()} />
}
