import { TextLink } from '../../shared/ui/TextLink'
import { useState } from 'react'
import { api, type TriggerReviewCard, type TriggerReviewDecision, type TriggerReviewResult } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { readableErrText } from '../../shared/data/errText'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { relPast } from './triggerMeta'
import { runFlashMeta } from '../schedule/scheduleMeta'

export function reviewDecision(card: TriggerReviewCard, decision: TriggerReviewDecision) {
  return { trigger_id: card.trigger_id, review_id: card.id, decision, expected_revision: card.action_revision }
}

export function announceReviewResult(card: TriggerReviewCard, result: TriggerReviewResult) {
  const message = result.summary || runFlashMeta(result.status || (result.outcome === 'dismissed' ? 'dismissed' : undefined)).label
  window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level: 'info', message: `${card.trigger_name || 'Automation'}: ${message}` } }))
}

export function TriggerReview({ onOpenTrigger, onChanged }: { onOpenTrigger: (id: string) => void; onChanged: () => void }) {
  const query = useQuery('triggers:review', () => api.triggerReview().then(result => result.cards))
  if (query.loading) return <p data-type="body-s" className="text-on-surface-low">Checking restart review…</p>
  if (!query.data?.length && !query.error) return null
  return <section aria-label="Restart review" className="mx-auto px-l py-l flex flex-col gap-m" style={{ maxWidth: 'var(--content-width)' }}>
    <h2 data-type="title-s">Waiting for you after restart</h2>
    {!!query.error && <InlineError icon onRetry={query.refresh}>Couldn’t load restart review: {readableErrText(query.error) || 'The gateway is unavailable.'}</InlineError>}
    {(query.data ?? []).map(card => <ReviewCard key={card.id} card={card} onOpenTrigger={onOpenTrigger}
      onChanged={() => { query.refresh(); onChanged() }} />)}
  </section>
}

export function ReviewCard({ card, onOpenTrigger, onChanged }: { card: TriggerReviewCard; onOpenTrigger: (id: string) => void; onChanged: () => void }) {
  const [busy, setBusy] = useState<TriggerReviewDecision | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [settled, setSettled] = useState(false)
  const decide = async (decision: TriggerReviewDecision) => {
    if (busy) return
    setBusy(decision)
    setError(null)
    try {
      const result = await api.decideTriggerReview(reviewDecision(card, decision))
      if (!result.ok) throw new Error('The review decision was refused.')
      announceReviewResult(card, result)
      setSettled(true)
      onChanged()
    } catch (failure) { setError(failure) }
    finally { setBusy(null) }
  }
  if (settled) return null
  return <article className="rounded-lg border border-outline-variant/50 bg-surface-container px-l py-m flex flex-col gap-m">
    <TextLink ink="emphasis" onClick={() => onOpenTrigger(card.trigger_id)} className="text-left">{card.trigger_name || 'Automation'}</TextLink>
    <p data-type="body-s" className="text-on-surface-var">{card.reason === 'interrupted'
      ? 'Interrupted during restart. It may already have produced an effect. Review it before running it again.'
      : `${card.missed_count} missed ${card.missed_count === 1 ? 'fire' : 'fires'}${card.latest_missed_at ? ` · latest ${relPast(card.latest_missed_at)}` : ''}. Run once now or dismiss these missed fires.`}</p>
    <div data-type="body-s"><span className="text-on-surface-low">Action: </span>{card.action.provider}
      {card.action.config.workflow ? ` · ${String(card.action.config.workflow)}` : ''}</div>
    {!!error && <InlineError icon>{readableErrText(error) || 'The decision could not be saved. Try again.'}</InlineError>}
    <div className="flex flex-wrap gap-s">
      <Button size="sm" variant="secondary" loading={busy === 'run_now'} loadingLabel="Running…" disabled={busy !== null} onClick={() => void decide('run_now')}>Run now</Button>
      <Button size="sm" variant="ghost" loading={busy === 'dismiss'} loadingLabel="Dismissing…" disabled={busy !== null} onClick={() => void decide('dismiss')}>Dismiss</Button>
    </div>
  </article>
}
