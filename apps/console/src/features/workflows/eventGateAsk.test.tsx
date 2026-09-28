import { useState } from 'react'
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WorkflowAsk } from './WorkflowAsk'
import { isTerminal, isNodeTerminal, runLook, nodeLook } from './workflowMeta'
import type { WorkflowContinuation } from '../../shared/data/api'

const PARKED = 'This monitor is parked between checks. It wakes when its self-scheduled trigger fires.'

function continuation(kind: string, prompt = PARKED): WorkflowContinuation {
  return {
    resume_token: 'tok-1', node_id: 'park',
    instance_path: 'root.children[1].body@0.children[0]',
    ask: { kind, prompt }, handoff: {},
    expires_at: Date.now() / 1000 + 600, expired: false,
  }
}

function AnswerPanel({ request }: { request: WorkflowContinuation }) {
  const [answer, setAnswer] = useState('')
  return <>
    <WorkflowAsk continuation={request} runId="r1" busy={false}
      onAnswer={(received, value, remember) => setAnswer(JSON.stringify({ token: received.resume_token, value, remember }))} />
    <output aria-label="Submitted answer">{answer}</output>
  </>
}

describe('an event gate', () => {
  it('offers only to wake it, and the wake remembers nothing', () => {
    const { getByText, queryByText, queryByLabelText, getByLabelText } = render(<AnswerPanel request={continuation('event')} />)
    expect(getByText(PARKED)).toBeInTheDocument()
    expect(getByText(/waits for something to happen, and the run carries on when it does/)).toBeInTheDocument()
    expect(queryByText('Approve')).toBeNull()
    expect(queryByText('Deny')).toBeNull()
    expect(queryByText(/Deny ends the run here/)).toBeNull()
    expect(queryByLabelText("Don't ask again for this step in this run")).toBeNull()
    fireEvent.click(getByText('Wake it now').closest('button')!)
    expect(getByLabelText('Submitted answer').textContent).toBe('{"token":"tok-1","value":true,"remember":false}')
  })

  it('leaves an approval gate as it was', () => {
    const { getByText, getByLabelText, queryByText } = render(<AnswerPanel request={continuation('approval', 'Publish the draft?')} />)
    expect(getByText('Approve')).toBeInTheDocument()
    expect(getByText('Deny')).toBeInTheDocument()
    expect(getByLabelText("Don't ask again for this step in this run")).toBeInTheDocument()
    expect(queryByText('Wake it now')).toBeNull()
    fireEvent.click(getByText('Deny').closest('button')!)
    expect(getByLabelText('Submitted answer').textContent).toBe('{"token":"tok-1","value":false,"remember":false}')
  })
})

it('declined outcomes are terminal native workflow states', () => {
  expect(isTerminal('declined')).toBe(true)
  expect(isNodeTerminal('declined')).toBe(true)
  expect(runLook('declined').label).toBe('Declined')
  expect(nodeLook('declined').label).toBe('Declined')
  expect(isTerminal('running')).toBe(false)
  expect(isNodeTerminal('waiting')).toBe(false)
})
