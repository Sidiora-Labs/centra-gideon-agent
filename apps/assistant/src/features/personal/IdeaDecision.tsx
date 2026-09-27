import React, { useEffect, useRef, useState } from 'react'
import { gatewayJson } from '../../shared/transport.web'
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

type Props = Readonly<{
  idea: ProjectedIdea
  decision?: IdeaDecisionRecord
  onDecision: (record: IdeaDecisionRecord) => void
}>

function newRequestId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function IdeaDecision({ idea, decision, onDecision }: Props) {
  const [prompt, setPrompt] = useState(decision?.edited_prompt ?? idea.title)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const requestId = useRef('')
  const ideaKey = `${idea.listId}:${idea.memberId}`

  useEffect(() => {
    setPrompt(decision?.edited_prompt ?? idea.title)
  }, [ideaKey, idea.title, decision?.edited_prompt])

  useEffect(() => {
    setMessage('')
    requestId.current = ''
  }, [ideaKey, idea.title])

  async function decide(action: 'accept' | 'dismiss') {
    if (busy || decision || !idea.revisionHash) return
    if (action === 'accept' && !prompt.trim()) {
      setMessage('Add a task prompt before accepting this Idea.')
      return
    }
    if (!requestId.current) requestId.current = newRequestId()
    setBusy(true)
    setMessage('')
    try {
      const saved = await gatewayJson<IdeaDecisionRecord>(`/api/assistant/ideas/${encodeURIComponent(idea.memberId)}/decision`, {
        method: 'POST',
        body: {
          source_kind: idea.sourceKind,
          source_list_id: idea.listId,
          expected_revision: idea.revisionHash,
          decision: action,
          edited_prompt: action === 'accept' ? prompt.trim() : '',
          request_id: requestId.current,
        },
      })
      onDecision(saved)
      setMessage(action === 'accept' ? `Accepted and linked task ${saved.task_id}.` : 'Idea dismissed and saved.')
      requestId.current = ''
    } catch (error) {
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
      <textarea id={`idea-task-prompt-${idea.memberId}`} rows={2} value={prompt} onChange={event => setPrompt(event.currentTarget.value)} disabled={busy} />
      {!idea.revisionHash && <p role="status">This source has no revision reference, so its decision controls are unavailable.</p>}
      <div className="gideon-idea-decision__actions">
        <button type="button" onClick={() => void decide('accept')} disabled={busy || !idea.revisionHash}>Accept as task</button>
        <button type="button" onClick={() => void decide('dismiss')} disabled={busy || !idea.revisionHash}>Dismiss</button>
      </div>
    </>}
    {message && <p role="status">{message}</p>}
  </section>
}

export default IdeaDecision
