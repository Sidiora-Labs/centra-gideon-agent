import { useEffect, useState } from 'react'
import { FieldError } from '../../shared/ui/forms'
import { api, type InboxItem, type WorkflowContinuation } from '../../shared/data/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'
import { useInboxOperation } from './inboxQueueState'

export function TriggerParkActions({ item, onChanged }: { item: InboxItem; onChanged: () => void }) {
  const refs = (item.refs ?? {}) as Record<string, unknown>
  const card = (refs.needs_input && typeof refs.needs_input === 'object' ? refs.needs_input : {}) as Record<string, unknown>
  const triggerId = typeof refs.trigger === 'string' ? refs.trigger : typeof refs.trigger_park === 'string' ? refs.trigger_park : ''
  const token = typeof card.resume_token === 'string' ? card.resume_token : typeof refs.resume_token === 'string' ? refs.resume_token : ''
  const attempted = Array.isArray(card.attempted) ? card.attempted.map(String) : []
  const continuation: WorkflowContinuation = {
    resume_token: token, node_id: '', instance_path: '',
    ask: { kind: 'approval', prompt: typeof card.blocker === 'string' ? card.blocker : item.message, rerun: true },
    handoff: { attempted }, expires_at: 0, expired: false,
  }
  const action = useInboxOperation(`${item.id}:${token}`)
  const [done, setDone] = useState('')
  useEffect(() => { setDone('') }, [item.id, token])
  const answer = (_request: WorkflowContinuation, value: unknown) => action.run(
    token,
    () => api.answerTriggerPark(triggerId, { resume_token: token, answer: value === true }),
    result => {
      if (result.refused) { action.setErr(result.refused); return }
      setDone(value !== true
        ? 'Declined. It asks again the next time it stops.'
        : result.waiting
          ? 'Ran it again, and it stopped for you again — its new question is in your Inbox.'
          : result.ok
            ? 'Ran it again — see its history for what it did.'
            : `Ran it again, and it ${result.result ?? 'did not finish'}.`)
      onChanged()
    },
    'Could not answer this',
  )
  if (!triggerId || !token) return <FieldError>This request no longer has an answer token. Refresh the Inbox.</FieldError>
  if (done) return <p data-type="body-s" className="text-on-surface-var">{done}</p>
  return <div className="flex flex-col gap-m">
    <WorkflowAsk continuation={continuation} runId="" busy={Boolean(action.busy)} onAnswer={answer}
      rerunCaption="Approve runs it again now. Deny leaves it until it next runs." />
    {action.err && <FieldError>{action.err}</FieldError>}
  </div>
}
