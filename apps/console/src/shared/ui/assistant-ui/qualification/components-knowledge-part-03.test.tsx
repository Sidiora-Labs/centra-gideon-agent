import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import type { Artifact, KnowledgeItem, TaskGraphData } from '../../../data/api'
import { KnowledgeMediaPlayer } from '../../../../features/chat/auiKnowledgeResults'
import { ArtifactDiagram, ArtifactMetricChart, ArtifactWebPreview, TaskFlowGraph } from '../../../../features/chat/auiStructuredResults'
import { FlowGraph } from '../../../vendor/assistant-ui/elements/flow-graph'

const json: Artifact = {
  slug: 'measurements', name: 'Run measurements', kind: 'json', source: 'chat', description: '', tags: [],
  version: 2, created_at: '2026-09-26', updated_at: '2026-09-26', events: [], source_path: '', readonly: false,
  content: '[{"latency":12},{"latency":0},{"latency":17}]',
}
const graph: TaskGraphData = {
  tasks: [{ id: 'task-a', title: 'Collect evidence', status: 'done' }, { id: 'task-b', title: 'Write report', status: 'blocked' }],
  edges: [{ from: 'task-a', to: 'task-b', type: 'BLOCKS' }],
  analysis: { completion_pct: 50, leaf_task_ids: ['task-b'], root_task_ids: ['task-a'], critical_path: ['task-a', 'task-b'], cycles: [] },
}
const audio: KnowledgeItem = { id: 'audio/1', title: 'Recorded briefing', type: 'audio', file_path: 'audio/briefing.wav' }

function GraphHarness() {
  const [selected, select] = useState('')
  return <><TaskFlowGraph graph={graph} onOpen={select} /><output aria-label="Selected task">{selected}</output></>
}

describe('artifact metrics', () => {
  it('draws only numbers from a real JSON artifact field', () => {
    render(<ArtifactMetricChart artifact={json} column="latency" />)
    expect(screen.getByRole('img', { name: 'latency: 17' })).toBeTruthy()
    expect(screen.getByLabelText('17')).toBeTruthy()
    expect(screen.queryByText('No numeric latency series in Run measurements.')).toBeNull()
  })

  it('keeps missing and invalid numeric series distinct from zero', () => {
    const { rerender } = render(<ArtifactMetricChart artifact={json} column="cost" />)
    expect(screen.getByText('No numeric cost series in Run measurements.')).toBeTruthy()
    rerender(<ArtifactMetricChart artifact={{ ...json, content: '[{"latency":null}]' }} column="latency" />)
    expect(screen.getByText('No numeric latency series in Run measurements.')).toBeTruthy()
    rerender(<ArtifactMetricChart artifact={{ ...json, content: '[{"latency":0}]' }} column="latency" />)
    expect(screen.getByRole('img', { name: 'latency: 0' })).toBeTruthy()
  })
})

describe('recorded media', () => {
  it('plays an uploaded audio knowledge file by its encoded ID', () => {
    const { container } = render(<KnowledgeMediaPlayer item={audio} />)
    expect(container.querySelector('audio')?.getAttribute('src')).toBe('/api/knowledge/items/audio%2F1/file')
    expect(container.querySelector('audio')?.hasAttribute('controls')).toBe(true)
  })

  it('plays a recorded video URL only when the item is video', () => {
    const { container } = render(<KnowledgeMediaPlayer item={{ ...audio, type: 'video', file_path: undefined, url: 'https://media.example.org/run.mp4' }} />)
    expect(container.querySelector('video')?.getAttribute('src')).toBe('https://media.example.org/run.mp4')
    expect(screen.getByText('Recorded briefing')).toBeTruthy()
  })

  it('does not claim absent or unsafe media', () => {
    const { container, rerender } = render(<KnowledgeMediaPlayer item={{ ...audio, file_path: undefined }} />)
    expect(container.querySelector('figure')).toBeNull()
    rerender(<KnowledgeMediaPlayer item={{ ...audio, file_path: undefined, url: 'javascript:alert(1)' }} />)
    expect(container.querySelector('figure')).toBeNull()
    rerender(<KnowledgeMediaPlayer item={{ ...audio, type: 'document' }} />)
    expect(container.querySelector('figure')).toBeNull()
  })
})

describe('saved artifact previews', () => {
  it('embeds a real HTML artifact with an inert sandbox and pinned version', () => {
    const { container } = render(<ArtifactWebPreview artifact={{ ...json, kind: 'html', slug: 'html/one' }} />)
    const frame = container.querySelector('iframe')
    expect(frame?.getAttribute('src')).toBe('/api/artifacts/html%2Fone/raw?version=2')
    expect(frame?.getAttribute('sandbox')).toBe('')
    expect(frame?.getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(screen.queryByRole('button', { name: 'Reload the preview' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open the preview in a new tab' })).toBeNull()
  })

  it('does not preview arbitrary kinds as HTML', () => {
    const { container } = render(<ArtifactWebPreview artifact={json} />)
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('renders a saved SVG diagram as an image at its actual version', () => {
    render(<ArtifactDiagram artifact={{ ...json, kind: 'svg', slug: 'graph/one' }} />)
    expect(screen.getByRole('img', { name: 'Run measurements' }).getAttribute('src'))
      .toBe('/api/artifacts/graph%2Fone/raw?version=2')
    expect(screen.queryByRole('button', { name: 'Zoom in' })).toBeNull()
  })

  it('does not present a JSON artifact as a diagram', () => {
    const { container } = render(<ArtifactDiagram artifact={json} />)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('persisted task flow', () => {
  it('shows the actual node names, statuses, and dependency path', () => {
    const { container } = render(<TaskFlowGraph graph={graph} />)
    expect(screen.getByText('Collect evidence')).toBeTruthy()
    expect(screen.getByText('Write report')).toBeTruthy()
    expect(screen.getByText('blocked')).toBeTruthy()
    expect(container.querySelector('svg path')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('opens the exact task ID when its handler is supplied', () => {
    render(<GraphHarness />)
    fireEvent.click(screen.getByRole('button', { name: 'Write report, blocked' }))
    expect(screen.getByRole('status', { name: 'Selected task' }).textContent).toBe('task-b')
  })

  it('ignores an edge without matching task records', () => {
    const { container } = render(<TaskFlowGraph graph={{ ...graph, edges: [{ from: 'missing', to: 'task-b', type: 'BLOCKS' }] }} />)
    expect(container.querySelector('svg path')).toBeNull()
  })

  it('renders the adopted flow primitive from explicit task node and edge data', () => {
    const { container } = render(<FlowGraph nodes={[
      { id: 'task-a', label: 'Collect evidence', column: 0, row: 0, state: 'done' },
      { id: 'task-b', label: 'Write report', column: 1, row: 0, state: 'active' },
    ]} edges={[{ from: 'task-a', to: 'task-b' }]} visibleCount={2} />)
    expect(screen.getByText('Collect evidence')).toBeTruthy()
    expect(screen.getByText('Write report')).toBeTruthy()
    expect(container.querySelector('svg path')).toBeTruthy()
  })
})
