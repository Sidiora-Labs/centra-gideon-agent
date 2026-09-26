import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import type { Artifact, ExperimentCampaign, KnowledgeContextResult, WorkflowRunStats } from '../../../data/api'
import { KnowledgeSources } from '../../../../features/chat/auiKnowledgeResults'
import {
  ExperimentScoreBreakdown, MeasuredQuotaBanner, RecordedArtifactImage,
  RunTraceWaterfall, WorkflowCostMeter,
} from '../../../../features/chat/auiStructuredResults'

const stats: WorkflowRunStats = {
  run_id: 'workflow-5', tokens: 120, tokens_recorded: true, cached_tokens: 20, cost_usd: 0.0125,
  priced: true, steps_completed: 2, steps_failed: 0, steps_cached: 0, duration_secs: 5,
  first_byte_ms: 10, models: ['provider/model'], unverified_steps: 0, verification_debt: 0, cache_hit_rate: 0,
}
const campaign: ExperimentCampaign = {
  id: 'campaign-1', title: 'Routing study', objective: 'Find a route', workflow_name: 'route', metric: 'latency',
  direction: 'minimize', max_parallel: 2, max_tokens: 1000, total_tokens: null, status: 'running', best_attempt: null,
  attempts: [
    { ordinal: 1, inputs: {}, state: 'complete', run_id: 'run-a', score: 32, valid: true, observation: '' },
    { ordinal: 2, inputs: {}, state: 'failed', run_id: 'run-b', score: null, valid: false, observation: '' },
  ], created_at: '2026-09-26',
}
const result: KnowledgeContextResult = {
  query: 'migration', total_tokens: 12, max_tokens: 4000,
  results: [{ id: 'note-1', title: 'Migration note', tokens: 12, summary: 'Real source' }],
}
const artifact: Artifact = {
  slug: 'image/one', name: 'Diagram image', kind: 'image', source: 'chat', description: '', tags: [], version: 4,
  created_at: '2026-09-26', updated_at: '2026-09-26', events: [], source_path: '', readonly: false,
}

function SourcesHarness() {
  const [selected, select] = useState('')
  return <><KnowledgeSources result={result} onOpen={select} /><output aria-label="Selected source">{selected}</output></>
}

describe('recorded trace timing', () => {
  it('renders only spans with actual start and end timestamps', () => {
    render(<RunTraceWaterfall spans={[
      { id: 'root', name: 'Run', startedAtMs: 1000, endedAtMs: 1500, status: 'completed' },
      { id: 'child', name: 'Tool', parentId: 'root', startedAtMs: 1100, endedAtMs: 1300, status: 'completed' },
    ]} />)
    expect(screen.getByText('500ms')).toBeTruthy()
    expect(screen.getByRole('img', { name: 'completed, starts at 100ms, runs 200ms' })).toBeTruthy()
  })

  it('does not convert missing timing into an invented span', () => {
    render(<RunTraceWaterfall spans={null} />)
    expect(screen.getByText('Span timing unavailable.')).toBeTruthy()
    expect(screen.queryByText('0ms')).toBeNull()
  })

  it('rejects reversed and nonfinite time bounds', () => {
    render(<RunTraceWaterfall spans={[
      { id: 'bad-order', name: 'Bad', startedAtMs: 20, endedAtMs: 10, status: 'failed' },
      { id: 'bad-time', name: 'NaN', startedAtMs: Number.NaN, endedAtMs: null, status: 'running' },
    ]} />)
    expect(screen.getByText('Span timing unavailable.')).toBeTruthy()
  })
})

describe('measured cost and quota', () => {
  it('shows priced workflow cost and recorded tokens', () => {
    render(<WorkflowCostMeter stats={stats} />)
    expect(screen.getByText('$0.0125')).toBeTruthy()
    expect(screen.getByText('120 tokens')).toBeTruthy()
    expect(screen.getByText('Run workflow-5')).toBeTruthy()
  })

  it('distinguishes unpriced from a measured zero cost', () => {
    const { rerender } = render(<WorkflowCostMeter stats={{ ...stats, cost_usd: 0, priced: false, tokens_recorded: false }} />)
    expect(screen.getByText('Cost unavailable')).toBeTruthy()
    expect(screen.getByText('Token usage unavailable')).toBeTruthy()
    rerender(<WorkflowCostMeter stats={{ ...stats, cost_usd: 0 }} />)
    expect(screen.getByText('$0.0000')).toBeTruthy()
  })

  it('shows the recorded quota and its actual reset timestamp', () => {
    render(<MeasuredQuotaBanner quota={{ used: 7, limit: 10, unit: 'calls', resetsAt: '2026-09-27T00:00:00Z' }} />)
    expect(screen.getByText('3 calls remaining')).toBeTruthy()
    expect(screen.getByRole('meter', { name: 'calls used' }).getAttribute('value')).toBe('7')
    expect(screen.getByText('Resets 2026-09-27T00:00:00Z')).toBeTruthy()
  })

  it('does not turn absent quota or zero limit into zero remaining', () => {
    const { rerender } = render(<MeasuredQuotaBanner quota={null} />)
    expect(screen.getByText('Quota unavailable')).toBeTruthy()
    rerender(<MeasuredQuotaBanner quota={{ used: 0, limit: 0, unit: 'calls' }} />)
    expect(screen.getByText('Quota unavailable')).toBeTruthy()
    rerender(<MeasuredQuotaBanner quota={{ used: null, limit: 10, unit: 'calls' }} />)
    expect(screen.getByText('Quota unavailable')).toBeTruthy()
  })
})

describe('scores, sources, and images', () => {
  it('shows exact campaign attempt scores without inventing a percentage', () => {
    render(<ExperimentScoreBreakdown campaign={campaign} />)
    expect(screen.getByText('Metric: latency · minimize')).toBeTruthy()
    expect(screen.getByText('Attempt 1: 32')).toBeTruthy()
    expect(screen.getByText(/Attempt 2: Score unavailable/)).toBeTruthy()
    expect(screen.getByText('· invalid')).toBeTruthy()
  })

  it('passes the recorded knowledge source ID to its opener', () => {
    render(<SourcesHarness />)
    fireEvent.click(screen.getByRole('button', { name: 'Open source Migration note' }))
    expect(screen.getByRole('status', { name: 'Selected source' }).textContent).toBe('note-1')
  })

  it('renders the exact version of a saved image artifact', () => {
    render(<RecordedArtifactImage artifact={artifact} />)
    expect(screen.getByRole('img', { name: 'Diagram image' }).getAttribute('src'))
      .toBe('/api/artifacts/image%2Fone/raw?version=4')
    expect(screen.getByText('Diagram image · version 4')).toBeTruthy()
  })

  it('does not claim a non-image artifact is an image', () => {
    const { container } = render(<RecordedArtifactImage artifact={{ ...artifact, kind: 'json' }} />)
    expect(container.querySelector('img')).toBeNull()
  })
})
