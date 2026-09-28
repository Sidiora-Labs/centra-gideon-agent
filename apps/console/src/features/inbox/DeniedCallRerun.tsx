import { useEffect, useState } from 'react'
import { AlertTriangle, ExternalLink, Play, RotateCcw } from 'lucide-react'
import { api, ApiError, type InboxItem, type Trigger, type WorkflowCascadePreview, type WorkflowRunDetailData } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { confirm } from '../../shared/ui/dialog'
import { cascadeConfirmation } from '../workflows/revalidate'

type Props = { item: InboxItem; onChanged: () => void; navigate: (path: string) => void }
type TriggerMatch = { kind: Trigger['kind']; rawId: string; trigger?: Trigger }

export function matchesTriggerReference(ref: string, rows: Trigger[]): TriggerMatch | null {
  const sep = ref.indexOf(':')
  const prefix = sep > 0 ? ref.slice(0, sep) : ''
  const rawId = prefix ? ref.slice(sep + 1) : ref
  const row = rows.find((candidate) =>
    (!prefix || candidate.kind === prefix) &&
    (candidate.id === ref || candidate.raw_id === ref || candidate.raw_id === rawId),
  )
  if (!row || (prefix && prefix !== row.kind)) return null
  return { kind: row.kind, rawId: row.raw_id, trigger: row }
}

export function isRetryableWorkflowStep(run: WorkflowRunDetailData | null, nodeId: string): boolean {
  return Boolean(run && ['running', 'paused', 'needs_input'].includes(run.status) &&
    nodeId && run.nodes.some((node) => node.node_id === nodeId))
}

function sampleForEvent(trigger: Trigger) {
  const pattern = trigger.pattern || 'MemoryUpdate'
  const matcher = pattern === 'MemoryKeyPattern' ? trigger.key_glob || ''
    : pattern === 'ContentMatch' ? trigger.content_re || ''
      : pattern === 'AppEvent' ? trigger.event_glob || ''
        : pattern === 'InboxSender' ? trigger.sender_glob || ''
          : pattern === 'InboxAddress' ? trigger.address_glob || '' : ''
  const meta: Record<string, string> | undefined = pattern === 'InboxSender' ? { sender: matcher || 'manual' }
    : pattern === 'InboxAddress' ? { address: matcher || 'manual' } : undefined
  return {
    key: pattern === 'MemoryKeyPattern' ? matcher || 'manual' : 'manual',
    value: pattern === 'ContentMatch' ? matcher || 'manual' : 'manual',
    event_type: pattern === 'AppEvent' ? matcher || 'manual' : pattern,
    ...(meta ? { meta } : {}),
  }
}

function cascadePreview(error: unknown): WorkflowCascadePreview | null {
  if (!(error instanceof ApiError) || error.code !== 'confirmation_required') return null
  const detail = error.detail
  if (!detail || typeof detail !== 'object') return null
  const preview = (detail as { preview?: unknown }).preview
  return preview && typeof preview === 'object' ? preview as WorkflowCascadePreview : null
}

export function DeniedCallRerun({ item, onChanged, navigate }: Props) {
  const refs = item.refs && typeof item.refs === 'object' ? item.refs : {}
  const chat = typeof refs.chat === 'string' ? refs.chat : ''
  const triggerRef = typeof refs.trigger === 'string' ? refs.trigger : ''
  const runId = typeof refs.run === 'string' ? refs.run : ''
  const nodeId = typeof refs.node === 'string' ? refs.node : ''
  const [trigger, setTrigger] = useState<TriggerMatch | null>(null)
  const [run, setRun] = useState<WorkflowRunDetailData | null>(null)
  const [chatAvailable, setChatAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    if (chat) {
      api.chatSessions(false).then((sessions) => {
        if (live) setChatAvailable(sessions.some((session) => session.key === chat && session.lifecycle !== 'archived'))
      }).catch(() => { if (live) setChatAvailable(false) })
    } else {
      setChatAvailable(false)
    }
    if (triggerRef) {
      Promise.allSettled([api.storeTriggers(), api.eventTriggers(), api.hooks(), api.schedules()])
        .then(([stores, events, hooks, schedules]) => {
          if (!live) return
          const rows: Trigger[] = []
          if (stores.status === 'fulfilled') rows.push(...stores.value)
          if (events.status === 'fulfilled') rows.push(...events.value)
          if (hooks.status === 'fulfilled') rows.push(...hooks.value.map((hook) => ({
            ...hook, kind: 'lifecycle' as const, raw_id: hook.id, action: { provider: hook.provider, config: hook.provider_config },
          })))
          if (schedules.status === 'fulfilled') rows.push(...schedules.value.jobs.map((job) => ({
            ...job, kind: 'schedule' as const, raw_id: job.id, id: `schedule:${job.id}`, action: job.action || { provider: '', config: {} },
          }) as Trigger))
          setTrigger(matchesTriggerReference(triggerRef, rows))
        })
    }
    if (runId) {
      api.workflowRun(runId).then((value) => { if (live) setRun(value) })
        .catch((reason: unknown) => { if (live) setError(reason instanceof Error ? reason.message : 'Could not read the recorded workflow run.') })
    }
    return () => { live = false }
  }, [chat, runId, triggerRef])

  async function retryChat() {
    setBusy(true); setError('')
    try {
      await api.sendChat('Retry the exact tool call described by this unanswered approval.', chat, {
        _auto_denied_retry_note_id: item.id,
      })
      navigate(`#/chat/${encodeURIComponent(chat)}`)
      onChanged()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not retry this chat call.')
    } finally { setBusy(false) }
  }

  async function runTrigger() {
    if (!trigger) return
    setBusy(true); setError('')
    try {
      const result = trigger.kind === 'store'
        ? await api.runStoreTrigger(trigger.rawId, false, item.id)
        : trigger.kind === 'schedule'
          ? await api.runSchedule(trigger.rawId, false, item.id)
          : trigger.kind === 'event' && trigger.trigger
            ? await api.runEventTrigger(trigger.rawId, { ...sampleForEvent(trigger.trigger), _auto_denied_retry_note_id: item.id })
            : null
      if (!result) { setError('This trigger type has no direct Run now action.'); return }
      if (result.ok === false) { setError(('refused' in result ? result.refused : '') || (typeof result.result === 'string' ? result.result : '') || 'The trigger did not run.'); return }
      onChanged()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not run this trigger.')
    } finally { setBusy(false) }
  }

  async function rerunWorkflowStep() {
    if (!runId || !nodeId) return
    setBusy(true); setError('')
    const submit = (confirmCascade: boolean) => api.rewindWorkflowRun(runId, {
      node_id: nodeId,
      _auto_denied_retry_note_id: item.id,
      ...(confirmCascade ? { confirm_cascade: true } : {}),
    })
    try {
      await submit(false)
      onChanged()
    } catch (reason) {
      const preview = cascadePreview(reason)
      if (preview) {
        const accepted = await confirm({
          title: `Run from “${nodeId}”?`,
          body: cascadeConfirmation(preview),
          danger: true,
          confirmLabel: 'Run from this step',
        })
        if (accepted) {
          try { await submit(true); onChanged() }
          catch (retryError) { setError(retryError instanceof Error ? retryError.message : 'Could not restart this workflow step.') }
        }
      } else {
        setError(reason instanceof Error ? reason.message : 'Could not restart this workflow step.')
      }
    } finally { setBusy(false) }
  }

  const retryableStep = isRetryableWorkflowStep(run, nodeId)
  const unresolved = item.status === 'pending' || item.status === 'seen'

  if (!refs.auto_denied) return null
  return (
    <section className="grid gap-s rounded-xl border border-warning/30 bg-warning/5 p-m" aria-label="Recorded unanswered call">
      <div className="flex items-start gap-s">
        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0">
          <h3 data-type="label-m" className="text-on-surface">This call ended without an owner answer</h3>
          <p data-type="body-s" className="mt-1 text-on-surface-var">Re-entry uses the recorded origin. The server verifies the live owner, trigger, run, and step before acting.</p>
        </div>
      </div>
      {unresolved && chatAvailable && <Button size="sm" variant="secondary" disabled={busy} onClick={retryChat}>
        <RotateCcw size={14} /> Retry the exact call in this chat
      </Button>}
      {unresolved && chat && !chatAvailable && <p data-type="body-s" className="text-on-surface-low">The exact chat session is unavailable; this note cannot be retried in another session.</p>}
      {unresolved && triggerRef && (trigger ? (
        <Button size="sm" variant="secondary" disabled={busy} onClick={runTrigger}>
          <Play size={14} /> Run this trigger now
        </Button>
      ) : <p data-type="body-s" className="text-on-surface-low">The recorded trigger is unavailable or no longer has a Run now action.</p>)}
      {unresolved && runId && nodeId && (retryableStep ? (
        <Button size="sm" variant="secondary" disabled={busy} onClick={rerunWorkflowStep}>
          <RotateCcw size={14} /> Run this workflow step again
        </Button>
      ) : <p data-type="body-s" className="text-on-surface-low">This exact workflow step is no longer live. Open the recorded run to review its current state.</p>)}
      {runId && <Button size="sm" variant="ghost" onClick={() => navigate(`#/workflows/runs/${encodeURIComponent(runId)}`)}>
        <ExternalLink size={14} /> Open the recorded workflow run
      </Button>}
      {error && <p role="alert" data-type="body-s" className="text-error">{error}</p>}
    </section>
  )
}
