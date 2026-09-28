import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { expect, it } from 'vitest'
import { fireEvent, render, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../shared/data/api'
import { WorkflowRetryControl } from './WorkflowRetryControl'
import { retryWindow, runEscalations } from './retryMeta'
import { EscalationPanel } from './EscalationPanel'
import { NodeInspectBody } from './NodeInspectorDrawer'

function failedRun(retryable: boolean, retryAt: number | null = null): WorkflowRunDetailData {
  const escalation = { kind: 'escalation', node_id: 'sample', instance_path: 'root.children[0]', reason: 'not_retried', detail: 'all sampling calls failed', attempts: [{ attempt: 1, failure_class: retryable ? 'network' : 'user' }] }
  return { run_id: 'sample-run', workflow: 'best-of-n', status: 'failed', spec_version: 1,
    attention: escalation, escalations: [escalation], nodes: [
      { instance_path: 'root.children[0]', node_id: 'sample', state: 'failed', failure: { class: retryable ? 'network' : 'user', retryable, retry_at: retryAt } },
      { instance_path: 'root.children[1]', node_id: 'select', state: 'skipped' },
    ] }
}
it('permits only a failed run whose every escalated step is retryable and takes the latest provider window', () => {
  const run = failedRun(true, Date.now() / 1000 + 25)
  expect(retryWindow(run)?.retryAt).toBe(run.nodes[0].failure?.retry_at)
  expect(retryWindow(failedRun(false))).toBeNull()
  expect(retryWindow({ ...run, status: 'draft' })).toBeNull()
  const second = { kind: 'escalation', node_id: 'publish', instance_path: 'root.children[2]', reason: 'not_retried', detail: 'the provider rejected the credential', attempts: [] }
  run.escalations?.push(second)
  run.nodes.push({ node_id: 'publish', instance_path: 'root.children[2]', state: 'failed', failure: { retryable: false, class: 'permission' } })
  expect(retryWindow(run)).toBeNull()
  const ui = render(<>{runEscalations(run).map((record, index) => <EscalationPanel key={index} escalation={record} />)}</>)
  expect(ui.getByText(/provider rejected/)).toBeInTheDocument()
  expect(ui.getAllByRole('heading', { name: /Escalation diagnosis/ })).toHaveLength(2)
})
it('renders a disabled retry with the actual breaker countdown and does not navigate', () => {
  let opened = ''
  const ui = render(<WorkflowRetryControl run={failedRun(true, Date.now() / 1000 + 25)} onFresh={() => undefined} onOpenRun={id => { opened = id }} onError={() => undefined} />)
  const button = ui.getByRole('button', { name: 'Retry' })
  expect(button).toHaveAttribute('aria-disabled', 'true')
  expect(button).toHaveAttribute('title', expect.stringMatching(/Retry becomes available in 2[456]s/))
  fireEvent.click(button)
  expect(opened).toBe('')
})
it('uses the real API fresh read and reports its transport failure without navigation', async () => {
  let opened = ''
  function Harness() {
    const [error, setError] = useState('')
    return <><WorkflowRetryControl run={failedRun(true)} onFresh={() => undefined} onOpenRun={id => { opened = id }} onError={setError} />{error && <p role="alert">{error}</p>}</>
  }
  const ui = render(<Harness />)
  fireEvent.click(ui.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(ui.getByRole('alert')).toBeInTheDocument())
  expect(opened).toBe('')
  expect(ui.getByRole('button', { name: 'Retry' })).not.toHaveAttribute('aria-disabled', 'true')
})
it('renders terminal attempt usage with known zero, unreported values, and incomplete-call floors', () => {
  const ui = render(<NodeInspectBody data={{
    run_id: 'sample-run', node_id: 'sample', instance_path: 'root.children[0]', state: 'failed',
    cached: false, resolved_prompt: 'Summarize', resolved_inputs: {}, output: null,
    attempts: [
      { attempt: 1, kind: 'step_completed', tokens: 0, cost_usd: 0, model_calls_open: 0 },
      { attempt: 2, kind: 'step_failed', tokens: null, cost_usd: null, model_calls_open: 0 },
      { attempt: 3, kind: 'step_failed', tokens: 12, cost_usd: 0.1, model_calls_open: 1 },
    ], ledger_events: [],
  }} />)
  expect(ui.getByText('0 tokens · $0.000000')).toBeInTheDocument()
  expect(ui.getByText('tokens unknown · cost unknown')).toBeInTheDocument()
  expect(ui.getByText('at least 12 tokens · at least $0.100000 · 1 incomplete model call')).toBeInTheDocument()
  expect(ui.getByText('#3')).toBeInTheDocument()
})
