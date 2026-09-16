import { useEffect, useId, useReducer, useRef } from 'react'
import { ThumbsUp, ThumbsDown } from 'lucide-react'
import { api, type FeedbackTargetKind, type FeedbackProducer, type FeedbackRecordBody } from '../data/api'
import { cx } from './cx'
import { feedbackState, initialFeedback, type FeedbackVerdict } from './statusSurfaceState'

export function FeedbackThumbs({ targetKind, targetId, producer, snapshot, className }: {
  targetKind: FeedbackTargetKind; targetId: string; producer?: FeedbackProducer; snapshot?: Record<string, unknown>; className?: string
}) {
  const [state, dispatch] = useReducer(feedbackState, initialFeedback)
  const input = useRef<HTMLInputElement>(null)
  const down = useRef<HTMLButtonElement>(null)
  const writes = useRef(Promise.resolve())
  const panelId = useId()
  useEffect(() => {
    let current = true
    dispatch({ type: 'reset' })
    api.feedbackTarget(targetKind, targetId).then(result => {
      if (current) dispatch({ type: 'hydrate', verdict: result.verdict })
    }).catch(() => { if (current) dispatch({ type: 'failed' }) })
    return () => { current = false }
  }, [targetKind, targetId])
  useEffect(() => { if (state.editing) input.current?.focus() }, [state.editing])

  function record(verdict: FeedbackVerdict, reason = '') {
    const body: FeedbackRecordBody = {
      target_kind: targetKind, target_id: targetId, verdict, reason: reason || undefined, snapshot,
      producer_kind: producer?.producer_kind, producer_id: producer?.producer_id,
    }
    dispatch({ type: 'record', verdict })
    writes.current = writes.current.then(async () => {
      try { await api.recordFeedback(body) } catch { /* Feedback stays optimistic. */ }
    })
  }
  if (state.disabled) return null
  const controls = [
    { verdict: 'up' as const, icon: ThumbsUp, title: 'Mark accurate', name: 'Mark accurate', tone: 'text-ok' },
    { verdict: 'down' as const, icon: ThumbsDown, title: 'Mark wrong (tell me why)', name: 'Mark wrong — optionally tell me why', tone: 'text-danger' },
  ]
  return <span className={cx('relative inline-flex items-center gap-0.5 rounded-lg', className)}>
    {controls.map(control => {
      const selected = state.verdict === control.verdict
      const Icon = control.icon
      return <button key={control.verdict} ref={control.verdict === 'down' ? down : undefined} type="button"
        title={control.title} aria-label={control.name} aria-pressed={selected}
        aria-expanded={control.verdict === 'down' ? state.editing : undefined}
        aria-controls={control.verdict === 'down' && state.editing ? panelId : undefined}
        onClick={() => control.verdict === 'up' || selected ? record(control.verdict) : dispatch({ type: 'toggle' })}
        className={cx('inline-flex size-6 items-center justify-center rounded-md transition-colors hover:bg-surface-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
          selected ? control.tone : 'text-on-surface-low hover:text-on-surface')}>
        <Icon size={13} aria-hidden className={selected ? 'fill-current' : undefined} />
      </button>
    })}
    {state.editing && <>
      <div className="fixed inset-0 z-40" aria-hidden onClick={() => record('down', state.reason)} />
      <div id={panelId} className="absolute right-0 top-7 z-50 w-64 rounded-lg border border-outline-variant/40 bg-surface-container p-2 shadow-lg">
        <input ref={input} value={state.reason} maxLength={500} placeholder="Why was this wrong? (optional)"
          aria-label="Why was this wrong (optional)" onChange={event => dispatch({ type: 'reason', value: event.target.value })}
          onKeyDown={event => {
            if (event.key !== 'Enter' && event.key !== 'Escape') return
            event.preventDefault(); event.stopPropagation()
            if (event.key === 'Enter') record('down', state.reason)
            else dispatch({ type: 'cancel' })
            down.current?.focus()
          }} data-type="body-s"
          className="h-8 w-full rounded-md bg-surface px-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        <div data-type="caption" className="mt-1 text-on-surface-low">Enter to send · Esc to cancel · click away to skip</div>
      </div>
    </>}
  </span>
}
