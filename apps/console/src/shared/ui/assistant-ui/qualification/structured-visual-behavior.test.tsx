import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MapAnswer, type MapPin } from '../../../vendor/assistant-ui/elements/map-answer'
import { Chart } from '../../../vendor/assistant-ui/elements/chart'
import { DataTable, type ModelUsage } from '../../../vendor/assistant-ui/elements/data-table'
import { ComparisonCard, type ComparisonOption } from '../../../vendor/assistant-ui/elements/comparison-card'
import { Diagram } from '../../../vendor/assistant-ui/elements/diagram'
import { WebPreview } from '../../../vendor/assistant-ui/elements/web-preview'

const sites: MapPin[] = [
  { id: 'depot', label: 'Depot', detail: '52.5, 13.4', x: 12.5, y: 30 },
  { id: 'station', label: 'Station', detail: '52.6, 13.5', x: 82, y: 64 },
]

function SelectableMap({ pins = sites, route = true }: { pins?: readonly MapPin[]; route?: boolean }) {
  const [activeId, setActiveId] = useState(pins[0]?.id ?? '')
  return <MapAnswer pins={pins} activeId={activeId} route={route} onSelect={setActiveId} data-testid="answer-map" />
}

describe('MapAnswer: route, selection, and presentation', () => {
  it('connects supplied coordinates and keeps marker and list selection synchronized', async () => {
    const user = userEvent.setup()
    const view = render(<SelectableMap />)
    const map = view.getByTestId('answer-map')
    expect(map.querySelector('polyline')?.getAttribute('points')).toBe('12.5,30 82,64')
    expect(screen.getByRole('button', { name: 'Depot' })).toHaveStyle({ left: '12.5%', top: '30%' })
    expect(screen.getByRole('button', { name: 'Station' })).toHaveStyle({ left: '82%', top: '64%' })
    expect(screen.getByRole('button', { name: 'Depot' })).toHaveAttribute('aria-current', 'true')
    await user.click(screen.getByRole('button', { name: 'Station' }))
    expect(screen.getByRole('button', { name: 'Station' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'Depot' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('button', { name: /Station.*52.6, 13.5/ })).toHaveAttribute('aria-current', 'true')
    await user.click(screen.getByRole('button', { name: /Depot.*52.5, 13.4/ }))
    expect(screen.getByRole('button', { name: 'Depot' })).toHaveAttribute('aria-current', 'true')
  })

  it('selects a map marker with the keyboard, using the same action as a pointer click', async () => {
    const user = userEvent.setup()
    render(<SelectableMap />)
    const marker = screen.getByRole('button', { name: 'Station' })
    marker.focus()
    expect(document.activeElement).toBe(marker)
    await user.keyboard('{Enter}')
    expect(marker).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /Station.*52.6, 13.5/ })).toHaveAttribute('aria-current', 'true')
  })

  it('does not draw a route for one pin, an empty map, or route=false', () => {
    const one = render(<SelectableMap pins={sites.slice(0, 1)} />)
    expect(one.container.querySelector('polyline')).toBeNull()
    one.rerender(<SelectableMap pins={[]} />)
    expect(one.container.querySelector('polyline')).toBeNull()
    expect(one.container.querySelectorAll('button')).toHaveLength(0)
    one.rerender(<SelectableMap pins={sites} route={false} />)
    expect(one.container.querySelector('polyline')).toBeNull()
  })

  it('uses readable noninteractive markers and rows when no selection action exists', () => {
    const view = render(<MapAnswer pins={sites} activeId="station" route />)
    expect(view.container.querySelectorAll('button')).toHaveLength(0)
    expect(screen.getByRole('img', { name: 'Station' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByText('52.6, 13.5')).toBeTruthy()
    expect(view.container.querySelector('polyline')?.getAttribute('points')).toBe('12.5,30 82,64')
  })
})

describe('Chart: supplied data and render variants', () => {
  it('uses visibleCount to reveal only supplied points, retaining a truthful accessible summary', () => {
    const view = render(<Chart label="Throughput" value="16 jobs" delta="+4" points={[2, 4, 6]} visibleCount={2} />)
    const graph = screen.getByRole('img', { name: 'Throughput: 16 jobs' })
    expect(graph.querySelector('polyline')?.getAttribute('points')?.trim().split(' ')).toHaveLength(2)
    expect(graph.querySelector('circle')?.getAttribute('cx')).toBe('150')
    expect(graph.querySelector('path')?.getAttribute('d')).toContain('L 150,')
    expect(screen.getByText('+4')).toHaveClass('text-emerald-600')
    view.rerender(<Chart label="Throughput" value="16 jobs" delta="−2" points={[2, 4, 6]} visibleCount={3} />)
    expect(graph.querySelector('polyline')?.getAttribute('points')?.trim().split(' ')).toHaveLength(3)
    expect(graph.querySelector('circle')?.getAttribute('cx')).toBe('294')
    expect(screen.getByText('−2')).toHaveClass('text-red-600')
  })

  it('clamps point reveal at both ends without generating nonexistent points', () => {
    const view = render(<Chart label="Capacity" value="6" points={[2, 4, 6]} visibleCount={-10} variant="line" />)
    const graph = screen.getByRole('img', { name: 'Capacity: 6' })
    expect(graph.querySelector('polyline')?.getAttribute('points')?.trim().split(' ')).toHaveLength(1)
    expect(graph.querySelector('path')).toBeNull()
    view.rerender(<Chart label="Capacity" value="6" points={[2, 4, 6]} visibleCount={99} variant="line" />)
    expect(graph.querySelector('polyline')?.getAttribute('points')?.trim().split(' ')).toHaveLength(3)
    expect(graph.querySelector('circle')?.getAttribute('cx')).toBe('294')
  })

  it('renders bars from the same dataset and highlights only the last visible bar', () => {
    const view = render(<Chart label="Samples" value="3" points={[0, 2, 3]} visibleCount={2} variant="bars" />)
    const graph = screen.getByRole('img', { name: 'Samples: 3' })
    expect(graph.querySelector('polyline')).toBeNull()
    expect(graph.querySelector('path')).toBeNull()
    let bars = [...graph.querySelectorAll('rect')]
    expect(bars).toHaveLength(2)
    expect(bars[0]).not.toHaveClass('fill-blue-500')
    expect(bars[1]).toHaveClass('fill-blue-500')
    expect(Number(bars[0].getAttribute('height'))).toBeGreaterThanOrEqual(1)
    expect(bars[1]).toHaveStyle({ animationDelay: '40ms' })
    view.rerender(<Chart label="Samples" value="3" points={[0, 2, 3]} visibleCount={3} variant="bars" />)
    bars = [...graph.querySelectorAll('rect')]
    expect(bars).toHaveLength(3)
    expect(bars[1]).not.toHaveClass('fill-blue-500')
    expect(bars[2]).toHaveClass('fill-blue-500')
  })

  it('keeps an empty dataset empty and renders a single zero without invalid geometry', () => {
    const view = render(<Chart label="No measurements" value="unknown" points={[]} visibleCount={9} />)
    let graph = screen.getByRole('img', { name: 'No measurements: unknown' })
    expect(graph.querySelector('polyline')?.getAttribute('points')).toBe('')
    expect(graph.querySelector('circle')).toBeNull()
    expect(graph.querySelector('path')).toBeNull()
    view.rerender(<Chart label="Zero" value="0" points={[0]} visibleCount={1} variant="bars" />)
    graph = screen.getByRole('img', { name: 'Zero: 0' })
    const bar = graph.querySelector('rect')
    expect(bar).not.toBeNull()
    expect(Number(bar?.getAttribute('height'))).toBeGreaterThanOrEqual(1)
    expect(`${bar?.getAttribute('x')} ${bar?.getAttribute('y')}`).not.toContain('NaN')
  })
})

const usage: ModelUsage[] = [
  { name: 'Atlas', context: '32k', cost: '$0.02' },
  { name: 'Beacon', context: '128k', cost: '$0.18' },
]

describe('DataTable: actual supplied usage rows', () => {
  it('preserves row order and aligns each model with its own context and cost', () => {
    const view = render(<DataTable rows={usage} cycle={1} data-testid="usage" />)
    const table = view.getByTestId('usage')
    expect(within(table).getByText('Model')).toBeTruthy()
    expect(within(table).getByText('Context')).toBeTruthy()
    expect(within(table).getByText('Cost')).toBeTruthy()
    const atlas = screen.getByText('Atlas').parentElement!
    const beacon = screen.getByText('Beacon').parentElement!
    expect(atlas.textContent).toContain('Atlas32k$0.02')
    expect(beacon.textContent).toContain('Beacon128k$0.18')
    expect(atlas.compareDocumentPosition(beacon) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(atlas).toHaveStyle({ animationDelay: '0ms' })
    expect(beacon).toHaveStyle({ animationDelay: '80ms' })
  })

  it('replays row entrance on cycle change while keeping producer values', () => {
    const view = render(<DataTable rows={usage} cycle={4} />)
    const firstRow = screen.getByText('Atlas').parentElement
    view.rerender(<DataTable rows={usage} cycle={5} />)
    expect(screen.getByText('Atlas').parentElement).not.toBe(firstRow)
    expect(screen.getByText('Atlas').parentElement?.textContent).toContain('32k$0.02')
    expect(screen.getByText('Beacon').parentElement?.textContent).toContain('128k$0.18')
  })

  it('shows headers but no invented usage when rows are empty', () => {
    const view = render(<DataTable rows={[]} cycle={1} />)
    expect(screen.getByText('Model')).toBeTruthy()
    expect(screen.getByText('Context')).toBeTruthy()
    expect(screen.getByText('Cost')).toBeTruthy()
    expect(view.container.querySelectorAll('[style*="animation-delay"]')).toHaveLength(0)
    expect(screen.queryByText('Atlas')).toBeNull()
  })
})

const options: ComparisonOption[] = [
  { id: 'steady', name: 'Steady', headline: 'Predictable', traits: ['Fast setup', false, 'Lower cost'] },
  { id: 'flex', name: 'Flexible', headline: 'Configurable', traits: [false, 'More control', false] },
]
const traitLabels = ['Setup', 'Control', 'Cost']

describe('ComparisonCard: recommendation and trait mapping', () => {
  it('marks only the selected recommendation and shows why it was chosen', () => {
    const view = render(<ComparisonCard traitLabels={traitLabels} options={options} recommendedId="steady" reason="Lower operating cost for this team." />)
    const card = view.container.querySelector('[data-slot="comparison-card"]')!
    expect(within(card as HTMLElement).getByText('Steady').parentElement).toHaveTextContent('pick')
    expect(within(card as HTMLElement).getByText('Flexible').parentElement).not.toHaveTextContent('pick')
    expect(within(card as HTMLElement).getByText('Lower operating cost for this team.')).toBeTruthy()
    expect(within(card as HTMLElement).getByText('Fast setup')).toBeTruthy()
    expect(within(card as HTMLElement).getByText('Lower cost')).toBeTruthy()
    expect(within(card as HTMLElement).getByText('Control')).toBeTruthy()
    expect(within(card as HTMLElement).getByText('Setup')).toBeTruthy()
  })

  it('moves the recommendation when data changes and does not invent one for an unknown id', () => {
    const view = render(<ComparisonCard traitLabels={traitLabels} options={options} recommendedId="flex" reason="The request needs more control." />)
    expect(screen.getByText('Flexible').parentElement).toHaveTextContent('pick')
    expect(screen.getByText('Steady').parentElement).not.toHaveTextContent('pick')
    view.rerender(<ComparisonCard traitLabels={traitLabels} options={options} recommendedId="missing" reason="No recommendation yet." />)
    expect(screen.queryByText('pick')).toBeNull()
    expect(screen.getByText('No recommendation yet.')).toBeTruthy()
  })

  it('uses the supplied trait label for false or missing values without fabricating text', () => {
    const sparse: ComparisonOption[] = [{ id: 'only', name: 'Only', headline: 'Sparse', traits: ['Available'] }]
    render(<ComparisonCard traitLabels={traitLabels} options={sparse} recommendedId="only" reason="One candidate." />)
    expect(screen.getByText('Available')).toBeTruthy()
    expect(screen.getByText('Control')).toBeTruthy()
    expect(screen.getByText('Cost')).toBeTruthy()
    expect(screen.queryByText('Fast setup')).toBeNull()
  })
})

function ZoomableDiagram() {
  const [zoom, setZoom] = useState(1)
  const [expanded, setExpanded] = useState(false)
  return <>
    <Diagram title="Dependency graph" zoom={zoom} onZoomOut={() => setZoom(value => value - 0.25)}
      onZoomIn={() => setZoom(value => value + 0.25)} onReset={() => setZoom(1)}
      onExpand={() => setExpanded(true)}>
      <div data-testid="diagram-node">Work item A</div>
    </Diagram>
    {expanded && <output>Expanded graph requested</output>}
  </>
}

describe('Diagram: real control callbacks and content', () => {
  it('updates the visible scale on pointer controls and resets it', async () => {
    const user = userEvent.setup()
    render(<ZoomableDiagram />)
    const node = screen.getByTestId('diagram-node')
    expect(node.parentElement).toHaveStyle({ transform: 'scale(1)' })
    await user.click(screen.getByRole('button', { name: 'Zoom in' }))
    expect(screen.getByText('125%')).toBeTruthy()
    expect(node.parentElement).toHaveStyle({ transform: 'scale(1.25)' })
    await user.click(screen.getByRole('button', { name: 'Zoom out' }))
    expect(screen.getByText('100%')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Reset the view' }))
    expect(node.parentElement).toHaveStyle({ transform: 'scale(1)' })
    await user.click(screen.getByRole('button', { name: 'Open full screen' }))
    expect(screen.getByText('Expanded graph requested')).toBeTruthy()
  })

  it('activates zoom from the keyboard and omits controls with no supplied action', async () => {
    const user = userEvent.setup()
    render(<ZoomableDiagram />)
    const zoom = screen.getByRole('button', { name: 'Zoom in' })
    zoom.focus()
    await user.keyboard('{Enter}')
    expect(screen.getByText('125%')).toBeTruthy()
    await user.keyboard(' ')
    expect(screen.getByText('150%')).toBeTruthy()
    const staticView = render(<Diagram title="Read only graph" zoom={0.625}><span>Fixed node</span></Diagram>)
    expect(within(staticView.container).queryByRole('button')).toBeNull()
    expect(screen.getByText('63%')).toBeTruthy()
    expect(screen.getByText('Fixed node').parentElement).toHaveStyle({ transform: 'scale(0.625)' })
  })
})

describe('WebPreview: real preview state and caller-owned isolation', () => {
  it('shows loading state without discarding the supplied sandboxed frame', () => {
    const frame = <iframe title="Document preview" sandbox="allow-scripts" srcDoc="<p>Preview</p>" />
    const view = render(<WebPreview origin="https://example.test/document" loading>{frame}</WebPreview>)
    const preview = view.container.querySelector('[data-slot="web-preview"]')!
    const iframe = preview.querySelector('iframe')!
    expect(iframe.getAttribute('sandbox')).toBe('allow-scripts')
    expect(iframe.parentElement).toHaveAttribute('aria-hidden', 'true')
    expect(within(preview as HTMLElement).getByText('Loading preview')).toBeTruthy()
    expect(within(preview as HTMLElement).getByText('https://example.test/document')).toBeTruthy()
    view.rerender(<WebPreview origin="https://example.test/document" loading={false}>{frame}</WebPreview>)
    expect(preview.querySelector('iframe')).toBe(iframe)
    expect(iframe.parentElement).toHaveAttribute('aria-hidden', 'false')
    expect(screen.queryByText('Loading preview')).toBeNull()
  })

  it('dispatches reload and external-open actions through visible buttons and keyboard', async () => {
    const user = userEvent.setup()
    const onReload = vi.fn()
    const onOpenExternal = vi.fn()
    const view = render(<WebPreview origin="https://example.test" loading={false}
      onReload={onReload} onOpenExternal={onOpenExternal}><span>Live preview</span></WebPreview>)
    const reload = screen.getByRole('button', { name: 'Reload the preview' })
    reload.focus()
    await user.keyboard('{Enter}')
    expect(onReload).toHaveBeenCalledOnce()
    await user.click(screen.getByRole('button', { name: 'Open the preview in a new tab' }))
    expect(onOpenExternal).toHaveBeenCalledOnce()
    expect(view.container.querySelector('[data-slot="web-preview"]')).toHaveTextContent('Live preview')
    view.rerender(<WebPreview origin="https://example.test" loading><span>Live preview</span></WebPreview>)
    expect(screen.queryByRole('button', { name: 'Reload the preview' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open the preview in a new tab' })).toBeNull()
  })
})
