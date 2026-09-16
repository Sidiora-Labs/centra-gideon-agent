import { useCallback, useEffect, useRef, useState } from 'react'
import { FieldError } from '../../shared/ui/forms'
import { api, type WorkflowContinuation } from '../../shared/data/api'
import { WorkflowAsk } from '../workflows/WorkflowAsk'
import { TextLink } from '../../shared/ui/TextLink'
import { useInboxOperation } from './inboxQueueState'

export function WorkflowGateActions({ runId, nodeId, onChanged, navigate }: {
  runId: string
  nodeId?: string
  onChanged: () => void
  navigate: (path: string) => void
}) {
  const [requests, setRequests] = useState<WorkflowContinuation[] | null>(null)
  const generation = useRef(0)
  const action = useInboxOperation(`${runId}:${nodeId || ''}`)
  const load = useCallback(async () => {
    const version = ++generation.current
    let pending: WorkflowContinuation[] = []
    try {
      const result = await api.workflowContinuations(runId)
      pending = (result.continuations || []).filter(request => !nodeId || request.node_id === nodeId)
    } catch {  }
    if (version === generation.current) setRequests(pending)
  }, [runId, nodeId])
  useEffect(() => {
    setRequests(null)
    void load()
    return () => { generation.current += 1 }
  }, [load])
  const answer = (request: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => action.run(
    request.resume_token,
    async () => {
      await api.resumeWorkflowRun(runId, { answer: value, resume_token: request.resume_token, always_allow: alwaysAllow })
      onChanged()
      await load()
    },
    () => {},
    'Could not answer this gate',
  )
  if (requests === null) return <p data-type="body-s" className="text-on-surface-low">Loading the request…</p>
  if (!requests.length) return <p data-type="body-s" className="text-on-surface-low">
    This request was already answered.{' '}
    <TextLink onClick={() => navigate(`workflows/${runId}`)}>Open the run</TextLink>
  </p>
  return <div className="grid gap-m rounded-xl border border-outline/20 p-m">
    {requests.map(request => <WorkflowAsk key={request.resume_token} continuation={request} runId={runId}
      busy={Boolean(action.busy)} onAnswer={answer} />)}
    {action.err && <FieldError>{action.err}</FieldError>}
  </div>
}
