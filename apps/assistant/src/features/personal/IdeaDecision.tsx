import React, { useEffect, useRef, useState } from 'react'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import type { ProjectedIdea } from './ideaProjection'

export type IdeaDecisionRecord = Readonly<{
  source_kind: 'knowledge-idea-list'
  source_id: string
  source_list_id: string
  source_revision: string
  source_title: string
  source_evidence: string
  decision: 'accepted' | 'dismissed'
  edited_prompt: string
  task_id: string
  request_id: string
  updated_at: string
}>

export type PendingIdeaDecisionRecord = Readonly<{
  source_kind: 'knowledge-idea-list'
  source_id: string
  source_list_id: string
  source_revision: string
  source_title: string
  source_evidence: string
  decision: 'accepted'
  edited_prompt: string
  task_id: ''
  request_id: string
  created_at: string
}>

type FrozenIntent = Readonly<{
  key: string
  action: 'accept' | 'dismiss'
  body: { source_kind: string; source_list_id: string; expected_revision: string; decision: 'accept' | 'dismiss'; edited_prompt: string; request_id: string }
}>

type Props = Readonly<{
  idea: ProjectedIdea
  decision?: IdeaDecisionRecord
  pendingIntent?: PendingIdeaDecisionRecord
  onDecision: (record: IdeaDecisionRecord) => void
}>

function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function IdeaDecision({ idea, decision, pendingIntent, onDecision }: Props) {
  const [promptValue, setPromptValue] = useState<{ key: string; value: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [frozen, setFrozen] = useState<FrozenIntent | null>(null)
  const frozenRef = useRef(frozen)
  const ideaKey = `${idea.listId}:${idea.memberId}`
  const prompt = promptValue?.key === ideaKey ? promptValue.value : decision?.edited_prompt ?? pendingIntent?.edited_prompt ?? idea.title
  const pending = frozen?.key === ideaKey ? frozen : null

  useEffect(() => {
    setPromptValue(current => current?.key === ideaKey ? current : null)
    setMessage('')
    if (pendingIntent?.source_list_id === idea.listId && pendingIntent.source_id === idea.memberId) {
      const restored: FrozenIntent = {
        key: ideaKey,
        action: pendingIntent.decision === 'accepted' ? 'accept' : 'dismiss',
        body: {
          source_kind: pendingIntent.source_kind,
          source_list_id: pendingIntent.source_list_id,
          expected_revision: pendingIntent.source_revision,
          decision: pendingIntent.decision === 'accepted' ? 'accept' : 'dismiss',
          edited_prompt: pendingIntent.edited_prompt,
          request_id: pendingIntent.request_id,
        },
      }
      frozenRef.current = restored
      setFrozen(restored)
    } else if (frozenRef.current?.key !== ideaKey) {
      frozenRef.current = null
      setFrozen(null)
    }
  }, [ideaKey, idea.listId, idea.memberId, pendingIntent])

  async function decide(action: 'accept' | 'dismiss') {
    if (busy || decision || !idea.revisionHash) return
    const retry = frozenRef.current?.key === ideaKey ? frozenRef.current : null
    if (retry && retry.action !== action) return
    if (!retry && action === 'accept' && !prompt.trim()) {
      setMessage('Add a task prompt before accepting this Idea.')
      return
    }
    const intent = retry ?? {
      key: ideaKey,
      action,
      body: {
        source_kind: idea.sourceKind,
        source_list_id: idea.listId,
        expected_revision: idea.revisionHash,
        decision: action,
        edited_prompt: action === 'accept' ? prompt.trim() : '',
        request_id: newRequestId(),
      },
    }
    frozenRef.current = intent
    setFrozen(intent)
    setBusy(true)
    setMessage('')
    try {
      const saved = await gatewayJson<IdeaDecisionRecord>(`/api/assistant/ideas/${encodeURIComponent(idea.memberId)}/decision`, {
        method: 'POST',
        body: intent.body,
      })
      onDecision(saved)
      setMessage(action === 'accept' ? `Accepted and linked task ${saved.task_id}.` : 'Idea dismissed and saved.')
      frozenRef.current = null
      setFrozen(null)
    } catch (error) {
      if (error instanceof GatewayError && error.status >= 400 && error.status < 500 && error.code !== 'decision_pending') {
        frozenRef.current = null
        setFrozen(null)
      }
      setMessage(error instanceof Error ? error.message : 'The decision could not be saved. Your prompt and source evidence are still here.')
    } finally {
      setBusy(false)
    }
  }

  return <section className="gideon-idea-decision" aria-label={`Decision for ${idea.title}`}>
    <p><strong>Decision</strong> · {decision?.decision ?? 'New'}</p>
    {decision?.decision === 'accepted' ? <p>Task linked: <code>{decision.task_id}</code></p> : null}
    {!decision && <>
      <label htmlFor={`idea-task-prompt-${idea.memberId}`}>Task prompt</label>
      <textarea id={`idea-task-prompt-${idea.memberId}`} rows={2} value={prompt} onChange={event => setPromptValue({ key: ideaKey, value: event.currentTarget.value })} disabled={busy || !!pending} />
      {pending && <p role="status">The save result is uncertain. Retry the same {pending.action === 'accept' ? 'acceptance' : 'dismissal'} to recover it.</p>}
      {!idea.revisionHash && <p role="status">This source has no revision reference, so its decision controls are unavailable.</p>}
      <div className="gideon-idea-decision__actions">
        <button type="button" onClick={() => void decide('accept')} disabled={busy || !idea.revisionHash || (!!pending && pending.action !== 'accept')}>{pending?.action === 'accept' ? 'Retry acceptance' : 'Accept as task'}</button>
        <button type="button" onClick={() => void decide('dismiss')} disabled={busy || !idea.revisionHash || (!!pending && pending.action !== 'dismiss')}>{pending?.action === 'dismiss' ? 'Retry dismissal' : 'Dismiss'}</button>
      </div>
    </>}
    {message && <p role="status">{message}</p>}
  </section>
}

export default IdeaDecision
