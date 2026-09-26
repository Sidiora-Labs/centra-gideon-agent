import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import type { Artifact, KnowledgeItem, ResearchReport, SemanticEntry } from '../../../data/api'
import {
  KnowledgeDocumentReference, KnowledgeGeoMap, KnowledgeMemoryEntry,
  KnowledgeRelationMap, KnowledgeResearchReport,
} from '../../../../features/chat/auiKnowledgeResults'
import { artifactTableRows, StructuredArtifactTable } from '../../../../features/chat/auiStructuredResults'
import { DocumentReference } from '../../../vendor/assistant-ui/elements/document-reference'
import { MemoryChips } from '../../../vendor/assistant-ui/elements/memory-chips'
import { ResearchReport as DonorResearchReport } from '../../../vendor/assistant-ui/elements/research-report'
import { MapAnswer } from '../../../vendor/assistant-ui/elements/map-answer'
import { DataTable } from '../../../vendor/assistant-ui/elements/data-table'

const document: KnowledgeItem = {
  id: 'doc-15', title: 'Migration record', type: 'document', summary: 'The queue was drained.',
  provider: 'native', relations: [{ id: 'rel-1', source_name: 'Queue', target_name: 'Worker', relation_type: 'feeds' }],
}
const memory: SemanticEntry = { key: 'operator.preference', value_json: '"No overnight alerts"', source: 'user' }
const report: ResearchReport = {
  id: 'report-9', name: 'Queue report', prompt: 'Check queue health', schedule: { kind: 'cron', cron_expr: '0 8 * * *' },
  tz: 'UTC', source: { tags: [], window_secs: 0 }, context: null, citation_policy: 'cite-source-only',
  iteration_cap: 3, enabled: true, created_ts: 1_700_000_000, last_run_ts: null, last_status: '', last_error: '', watermark_ts: 0,
}
const artifact: Artifact = {
  slug: 'queue-report', name: 'Queue metrics', kind: 'json', source: 'chat', description: '', tags: [],
  version: 1, created_at: '2026-09-26T08:00:00Z', updated_at: '2026-09-26T08:00:00Z',
  content: '[{"worker":"a","processed":12,"healthy":true},{"worker":"b","processed":0,"healthy":false}]',
  events: [], source_path: '', readonly: false,
}

function OpenHarness({ child }: { child: (open: (id: string) => void) => React.ReactNode }) {
  const [opened, setOpened] = useState('')
  return <>{child(setOpened)}<output aria-label="Selected record">{opened}</output></>
}

describe('knowledge records', () => {
  it('shows a document title, summary, and recorded source', () => {
    render(<KnowledgeDocumentReference item={document} />)
    expect(screen.getByText('Migration record')).toBeTruthy()
    expect(screen.getByText('The queue was drained.')).toBeTruthy()
    expect(screen.getByText('Source: native')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('opens the original document ID when the destination exists', () => {
    render(<OpenHarness child={(open) => <KnowledgeDocumentReference item={document} onOpen={open} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Migration record' }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('doc-15')
  })

  it('renders a persisted semantic memory entry without invented confidence', () => {
    render(<KnowledgeMemoryEntry entry={memory} />)
    expect(screen.getByText('operator.preference')).toBeTruthy()
    expect(screen.getByText('"No overnight alerts"')).toBeTruthy()
    expect(screen.getByText('Source: user')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('reports a report that has never run without implying success', () => {
    render(<KnowledgeResearchReport report={report} />)
    expect(screen.getByText('Queue report')).toBeTruthy()
    expect(screen.getByText('Check queue health')).toBeTruthy()
    expect(screen.getByText('Last run: Never · No status')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('passes the persisted report ID to the supplied run action', () => {
    render(<OpenHarness child={(open) => <KnowledgeResearchReport report={report} onRun={open} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run report' }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('report-9')
  })

  it('shows a report failure returned by the API record', () => {
    render(<KnowledgeResearchReport report={{ ...report, last_status: 'error', last_error: 'provider unavailable' }} />)
    expect(screen.getByRole('alert').textContent).toBe('provider unavailable')
  })

  it('renders actual named relations and does not invent missing names', () => {
    render(<KnowledgeRelationMap item={document} />)
    expect(screen.getByText(/Queue → Worker/)).toBeTruthy()
    expect(screen.getByText(/feeds/)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('opens the knowledge record represented by the relation plot', () => {
    render(<OpenHarness child={(open) => <KnowledgeRelationMap item={document} onOpen={open} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open knowledge graph' }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('doc-15')
  })

  it('plots only supplied finite geographic coordinates', () => {
    render(<KnowledgeGeoMap points={[
      { id: 'place-1', label: 'Recorded site', latitude: 52.5, longitude: 13.4 },
      { id: 'invalid', label: 'Invalid site', latitude: 200, longitude: 0 },
    ]} />)
    expect(screen.getAllByRole('img', { name: 'Recorded site' })).toHaveLength(1)
    expect(screen.getByText('52.5, 13.4')).toBeTruthy()
    expect(screen.queryByText('Invalid site')).toBeNull()
  })

  it('does not fabricate a map when no coordinates exist', () => {
    const { container } = render(<KnowledgeGeoMap points={[]} />)
    expect(container.querySelector('[data-slot="geo-map"]')).toBeNull()
  })

  it('uses a real location ID for a supplied action', () => {
    render(<OpenHarness child={(open) => <KnowledgeGeoMap points={[
      { id: 'place-1', label: 'Recorded site', latitude: 52.5, longitude: 13.4 },
    ]} onOpen={open} />} />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Recorded site' })[0])
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('place-1')
  })
})

describe('JSON artifact data table', () => {
  it('reads scalar rows from a persisted JSON artifact', () => {
    expect(artifactTableRows(artifact)).toEqual([
      { worker: 'a', processed: 12, healthy: true }, { worker: 'b', processed: 0, healthy: false },
    ])
  })

  it('renders each recorded row and preserves zero and false', () => {
    render(<StructuredArtifactTable artifact={artifact} />)
    expect(screen.getByRole('table').querySelectorAll('tbody tr')).toHaveLength(2)
    expect(screen.getByText('0')).toBeTruthy()
    expect(screen.getByText('false')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('opens the exact artifact slug when an opener exists', () => {
    render(<OpenHarness child={(open) => <StructuredArtifactTable artifact={artifact} onOpen={open} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open artifact' }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('queue-report')
  })

  it('rejects malformed, nested, and non-JSON content', () => {
    expect(artifactTableRows({ ...artifact, content: '{' })).toBeNull()
    expect(artifactTableRows({ ...artifact, content: '[{"nested":{"x":1}}]' })).toBeNull()
    expect(artifactTableRows({ ...artifact, kind: 'markdown' })).toBeNull()
    expect(artifactTableRows({ ...artifact, content: '{}' })).toBeNull()
  })

  it('labels non-tabular artifacts honestly', () => {
    render(<StructuredArtifactTable artifact={{ ...artifact, content: '{"count":1}' }} />)
    expect(screen.getByText('No tabular JSON in Queue metrics.')).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('reports an actual empty table as no rows', () => {
    render(<StructuredArtifactTable artifact={{ ...artifact, content: '[]' }} />)
    expect(screen.getByText('No rows available.')).toBeTruthy()
  })
})

describe('adopted donor knowledge modules', () => {
  it('keeps recorded document anchors visible without an unused jump control', () => {
    render(<DocumentReference title="Migration record" pages={4} anchors={[{ page: 2, quote: 'Queue was drained' }]}
      activePage={2} />)
    expect(screen.getByText('Queue was drained')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('passes the chosen document page to a supplied navigation action', () => {
    render(<OpenHarness child={(open) => <DocumentReference title="Migration record" pages={4}
      anchors={[{ page: 2, quote: 'Queue was drained' }]} activePage={1} onJump={(page) => open(String(page))} />} />)
    fireEvent.click(screen.getByRole('button', { name: /Queue was drained/ }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('2')
  })

  it('marks actual memory chip changes without a forget action when absent', () => {
    render(<MemoryChips chips={[{ id: 'mem-1', text: 'Operator preference', change: 'added' }]} />)
    expect(screen.getByText('remembered 1')).toBeTruthy()
    expect(screen.getByText('Operator preference')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('passes the persisted memory ID to an actual forget handler', () => {
    render(<OpenHarness child={(open) => <MemoryChips chips={[{ id: 'mem-1', text: 'Operator preference', change: 'existing' }]}
      onForget={open} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Forget "Operator preference"' }))
    expect(screen.getByRole('status', { name: 'Selected record' }).textContent).toBe('mem-1')
  })

  it('reports exact sections and source counts supplied by a research producer', () => {
    render(<DonorResearchReport title="Queue report" sections={[
      { id: 'intro', heading: 'Summary', state: 'done', sources: 2 },
      { id: 'body', heading: 'Detail', state: 'writing', sources: 0 },
    ]} sourcesRead={2} />)
    expect(screen.getByText('1/2 sections · 2 sources read')).toBeTruthy()
    expect(screen.getByText('Summary')).toBeTruthy()
    expect(screen.getByText('Detail')).toBeTruthy()
  })

  it('uses supplied map positions and does not invent a route', () => {
    const { container } = render(<MapAnswer pins={[{ id: 'site-1', label: 'Recorded site', detail: '52.5, 13.4', x: 53.7, y: 20.8 }]}
      activeId="" />)
    expect(screen.getByText('52.5, 13.4')).toBeTruthy()
    expect(container.querySelector('polyline')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders actual model usage rows without a sample row', () => {
    render(<DataTable rows={[{ name: 'model-a', context: '8k', cost: '$0.04' }]} cycle={1} />)
    expect(screen.getByText('model-a')).toBeTruthy()
    expect(screen.getByText('8k')).toBeTruthy()
    expect(screen.getByText('$0.04')).toBeTruthy()
  })
})
