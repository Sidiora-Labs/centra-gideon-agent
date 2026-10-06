import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { KnowledgeIngestGraph, PhaseOutcome } from '../../shared/data/api'
import { PhaseOutcomes, outcomeSentence } from './PhaseOutcomes'
import { resolveNodePhases } from './KnowledgeDetail'
import { failedEnrichment } from './knowledgeMeta'

afterEach(cleanup)

const graph: KnowledgeIngestGraph = {
  item_type: 'image',
  nodes: [{ node_type: 'ocr', label: 'OCR' }, { node_type: 'vision', label: 'Vision' }, { node_type: 'insights', label: 'Insights' }],
  edges: [],
}

const missing: PhaseOutcome = { status: 'skipped', reason: 'No image model is set up.', needs: ['image_modality'], fix: [{ text: 'Choose a model for Image · Modality in Settings → Models', href: '#/settings/models' }] }

describe('Persisted knowledge outcomes', () => {
  it('shows the real reason and in-app setup route without treating an unused branch as failed', () => {
    render(<PhaseOutcomes graph={graph} phases={{ ocr: missing, vision: { status: 'not_applicable', reason: 'Another branch was chosen.' } }} />)
    expect(screen.getByText('No image model is set up.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Choose a model for Image · Modality in Settings → Models' }).getAttribute('href')).toBe('#/settings/models')
    expect(screen.queryByText('Another branch was chosen.')).toBeNull()
    expect(outcomeSentence('Vision', { status: 'not_applicable', reason: 'Another branch was chosen.' })).toBe('Vision: not needed — Another branch was chosen.')
  })

  it('offers an actual retry action only after a skipped prerequisite is ready', async () => {
    let calls = 0
    const user = userEvent.setup()
    const { rerender } = render(<PhaseOutcomes graph={graph} phases={{ ocr: missing }} onRunAgain={() => { calls += 1 }} />)
    expect(screen.queryByRole('button', { name: 'Run again' })).toBeNull()
    rerender(<PhaseOutcomes graph={graph} phases={{ ocr: { ...missing, ready: true } }} onRunAgain={() => { calls += 1 }} />)
    await user.click(screen.getByRole('button', { name: 'Run again' }))
    expect(calls).toBe(1)
  })

  it('renders external setup URLs as words instead of links', () => {
    render(<PhaseOutcomes graph={graph} phases={{ ocr: { ...missing, fix: [{ text: 'An untrusted destination', href: 'https://outside.invalid/' }] } }} />)
    expect(screen.getByText('An untrusted destination')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('uses persisted outcomes after reload and leaves missing observations pending', () => {
    const durable = JSON.parse(JSON.stringify({ ocr: missing, vision: { status: 'not_applicable' } }))
    expect(resolveNodePhases({ ...graph, node_phases: durable }, {}, 'done')).toEqual({ ocr: 'skipped', vision: 'not_applicable', insights: 'pending' })
    expect(resolveNodePhases({ ...graph, node_phases: durable }, { ocr: 'running' }, 'processing')).toEqual({ ocr: 'running', vision: 'pending', insights: 'pending' })
  })

  it('keeps persisted model failures distinct from skipped capability setup in list summaries', () => {
    expect(failedEnrichment({ processing_status: 'partial', file_metadata: { node_phases: { insights: { status: 'failed', reason: 'Provider unavailable.' }, entities: { status: 'done' } } } })?.reason).toBe('Insights: Provider unavailable.')
    expect(failedEnrichment({ processing_status: 'partial', file_metadata: { node_phases: { insights: missing } } })).toBeNull()
    expect(failedEnrichment({ processing_status: 'processing', file_metadata: { node_phases: { insights: { status: 'failed' } } } })).toBeNull()
  })
})

// These states distinguish a recorded empty graph from missing processing evidence.
describe('Recorded entity outcomes', () => {
  it('keeps missing extraction evidence unknown and offers no blind regeneration', async () => {
    const { KnowledgeGraphEmptyState } = await import('./KnowledgeGraph')
    render(<KnowledgeGraphEmptyState onRegenerate={() => { throw new Error('Should not run') }} />)
    expect(screen.getByText('No recorded extraction outcomes are available.')).toBeTruthy()
    expect(screen.queryByText(/not been through entity extraction/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Regenerate intelligence' })).toBeNull()
  })

  it('shows actual done, skipped and not-needed counts without equating them to never run', async () => {
    const { KnowledgeGraphEmptyState } = await import('./KnowledgeGraph')
    render(<KnowledgeGraphEmptyState extraction={{ running: 0, failed: 0, ran: 2, skipped: 1, not_applicable: 3, not_run: 0, total: 6 }} onRegenerate={() => { throw new Error('Should not run') }} />)
    expect(screen.getByText('Entity processing completed for 2 item(s). 1 item(s) skipped entity processing. 3 item(s) did not need entity processing.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Regenerate intelligence' })).toBeNull()
  })
})
