import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { FlowGraph, type FlowNode } from '../../../vendor/assistant-ui/elements/flow-graph'
import { JobProgress } from '../../../vendor/assistant-ui/elements/job-progress'
import { Timeline, type TimelineEvent } from '../../../vendor/assistant-ui/elements/timeline'
import { SpecSheet } from '../../../vendor/assistant-ui/elements/spec-sheet'
import { ScoreBreakdown } from '../../../vendor/assistant-ui/elements/score-breakdown'
import { NumberTicker } from '../../../vendor/assistant-ui/elements/number-ticker'
import { Frac, MathBlock, Sub, Sup } from '../../../vendor/assistant-ui/elements/math-block'

const nodes: FlowNode[] = [
  { id: 'collect', label: 'Collect', column: 0, row: 0, state: 'done' },
  { id: 'review', label: 'Review', column: 1, row: 1, state: 'active' },
  { id: 'publish', label: 'Publish', column: 2, row: 1, state: 'pending' },
]
const edges = [{ from: 'collect', to: 'review' }, { from: 'review', to: 'publish' }]
const stages = [
  { name: 'Collect', weight: 2 }, { name: 'Review', weight: 3 }, { name: 'Publish', weight: 5 },
]
const events: TimelineEvent[] = [
  { id: 'received', when: 'past', time: '09:10', title: 'Received', detail: 'Source uploaded' },
  { id: 'checking', when: 'now', time: '09:12', title: 'Checking', detail: 'Two files remaining' },
  { id: 'done', when: 'future', time: '—', title: 'Published' },
]

describe('FlowGraph progress from caller records', () => {
  it('reveals only requested nodes while the SVG preserves exact dependency geometry', () => {
    const { container, rerender } = render(<FlowGraph nodes={nodes} edges={edges} visibleCount={1} />)
    expect(screen.getByText('Collect')).toBeTruthy()
    expect(screen.queryByText('Review')).toBeNull()
    const canvas = container.querySelector('[data-slot="flow-graph"] > div') as HTMLElement
    expect(canvas.style.width).toBe('270px')
    expect(canvas.style.height).toBe('88px')
    const paths = Array.from(container.querySelectorAll('svg path'))
    expect(paths.map((path) => path.getAttribute('d'))).toEqual([
      'M 78 15 C 87 15, 87 73, 96 73',
      'M 174 73 C 183 73, 183 73, 192 73',
    ])
    expect(paths.every((path) => path.getAttribute('class')?.includes('stroke-foreground/5'))).toBe(true)
    rerender(<FlowGraph nodes={nodes} edges={edges} visibleCount={2} />)
    expect(screen.getByText('Review')).toBeTruthy()
    expect(screen.queryByText('Publish')).toBeNull()
    expect(paths[0].getAttribute('class')).toContain('stroke-foreground/20')
    expect(paths[1].getAttribute('class')).toContain('stroke-foreground/5')
    rerender(<FlowGraph nodes={nodes} edges={edges} visibleCount={3} />)
    expect(screen.getByText('Publish')).toBeTruthy()
    expect(paths[1].getAttribute('class')).toContain('stroke-foreground/20')
  })

  it('keeps distinct done, active, and pending treatments tied to node state', () => {
    render(<FlowGraph nodes={nodes} edges={edges} visibleCount={3} />)
    expect(screen.getByText('Collect').closest('div')?.className).toContain('bg-foreground/[0.04]')
    expect(screen.getByText('Review').closest('div')?.className).toContain('bg-blue-500/10')
    expect(screen.getByText('Publish').closest('div')?.className).toContain('border-dashed')
  })

  it('bounds partial and invalid reveal counts without inventing nodes or missing edges', () => {
    const { container, rerender } = render(<FlowGraph nodes={nodes} edges={edges} visibleCount={Number.NaN} />)
    expect(screen.queryByText('Collect')).toBeNull()
    rerender(<FlowGraph nodes={nodes} edges={edges} visibleCount={-2} />)
    expect(screen.queryByText('Collect')).toBeNull()
    rerender(<FlowGraph nodes={nodes} edges={[...edges, { from: 'absent', to: 'review' }]} visibleCount={99} />)
    expect(container.querySelectorAll('svg path')).toHaveLength(2)
    expect(screen.getByText('Publish')).toBeTruthy()
  })
})

describe('JobProgress weighted progress and completion', () => {
  it('announces weighted overall progress and updates it as a real stage advances', () => {
    const props = { title: 'Evidence run', stages, eta: '4 min' }
    const { container, rerender } = render(<JobProgress {...props} stageIndex={1} stageProgress={0.4} />)
    const bar = screen.getByRole('progressbar', { name: 'Evidence run progress' })
    expect(bar.getAttribute('aria-valuenow')).toBe('32')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('32%')
    expect(screen.getByText('4 min')).toBeTruthy()
    expect(screen.getByText('Review').className).toContain('text-foreground/90')
    rerender(<JobProgress {...props} stageIndex={2} stageProgress={0.5} />)
    expect(bar.getAttribute('aria-valuenow')).toBe('75')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('75%')
    expect(screen.getByText('Publish').className).toContain('text-foreground/90')
    expect(container.querySelector('[data-slot="job-progress"]')).toBeTruthy()
  })

  it('clamps a partial stage to its valid range before announcing and drawing', () => {
    const props = { title: 'Bounded run', stages, stageIndex: 1, eta: 'unknown' }
    const { rerender } = render(<JobProgress {...props} stageProgress={-1} />)
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('20')
    rerender(<JobProgress {...props} stageProgress={Number.NaN} />)
    expect(bar.getAttribute('aria-valuenow')).toBe('20')
    rerender(<JobProgress {...props} stageProgress={8} />)
    expect(bar.getAttribute('aria-valuenow')).toBe('50')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('50%')
  })

  it('finishes at 100 percent, removes cancellation, and reports done', () => {
    let cancellationRequested = false
    const requestCancellation = () => { cancellationRequested = true }
    const { rerender } = render(<JobProgress title="Publish run" stages={stages} stageIndex={2}
      stageProgress={0.8} eta="1 min" onCancel={requestCancellation} />)
    screen.getByRole('button', { name: 'Cancel the job' }).click()
    expect(cancellationRequested).toBe(true)
    rerender(<JobProgress title="Publish run" stages={stages} stageIndex={99}
      stageProgress={0} eta="1 min" onCancel={requestCancellation} />)
    const bar = screen.getByRole('progressbar')
    expect(bar.getAttribute('aria-valuenow')).toBe('100')
    expect((bar.firstElementChild as HTMLElement).style.width).toBe('100%')
    expect(screen.getByText('done')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Cancel the job' })).toBeNull()
  })

  it('does not announce progress for an empty plan', () => {
    render(<JobProgress title="Unplanned run" stages={[]} stageIndex={3} stageProgress={1} eta="unknown" />)
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0')
    expect(screen.getByText('done')).toBeTruthy()
  })
})

describe('Timeline and SpecSheet progressive disclosure', () => {
  it('reveals dated events in order and only connects visible rows', () => {
    const { container, rerender } = render(<Timeline events={events} visibleCount={1} />)
    expect(screen.getByText('09:10')).toBeTruthy()
    expect(screen.getByText('Source uploaded')).toBeTruthy()
    expect(screen.queryByText('Checking')).toBeNull()
    expect(container.querySelectorAll('.w-px')).toHaveLength(0)
    rerender(<Timeline events={events} visibleCount={3} />)
    expect(screen.getByText('Two files remaining')).toBeTruthy()
    expect(screen.getByText('Published')).toBeTruthy()
    expect(container.querySelectorAll('.w-px')).toHaveLength(2)
    expect(screen.getByText('Checking').className).toContain('font-medium')
    expect(screen.getByText('Published').className).toContain('text-foreground/40')
  })

  it('never exposes a future event when count is negative or invalid', () => {
    const { rerender } = render(<Timeline events={events} visibleCount={Number.NaN} />)
    expect(screen.queryByText('Received')).toBeNull()
    rerender(<Timeline events={events} visibleCount={-1} />)
    expect(screen.queryByText('Received')).toBeNull()
    rerender(<Timeline events={events} visibleCount={99} />)
    expect(screen.getAllByText(/Received|Checking|Published/)).toHaveLength(3)
  })

  it('dims the connector between two still-scheduled future events', () => {
    const scheduled: TimelineEvent[] = [
      events[0],
      { id: 'queued', when: 'future', time: '10:00', title: 'Queued review' },
      { id: 'release', when: 'future', time: '11:00', title: 'Scheduled release' },
    ]
    const { container } = render(<Timeline events={scheduled} visibleCount={3} />)
    const connectors = container.querySelectorAll('.w-px')
    expect(connectors).toHaveLength(2)
    expect(connectors[0].className).toContain('bg-foreground/15')
    expect(connectors[1].className).toContain('bg-foreground/[0.08]')
    expect(screen.getByText('Scheduled release')).toBeTruthy()
  })

  it('preserves supplied scalar values and emphasis as rows arrive', () => {
    const rows = [
      { label: 'Retries', value: '0' },
      { label: 'Validated', value: 'false' },
      { label: 'Latency', value: '12.5 ms', emphasis: true },
    ]
    const { rerender } = render(<SpecSheet title="Run facts" subtitle="Recorded values" rows={rows} visibleCount={1} />)
    expect(screen.getByText('Run facts')).toBeTruthy()
    expect(screen.getByText('Recorded values')).toBeTruthy()
    expect(screen.getByText('0')).toBeTruthy()
    expect(screen.queryByText('false')).toBeNull()
    rerender(<SpecSheet title="Run facts" subtitle="Recorded values" rows={rows} visibleCount={3} />)
    expect(screen.getByText('false')).toBeTruthy()
    expect(screen.getByText('12.5 ms').className).toContain('font-medium')
  })

  it('bounds missing and excessive spec rows without fabricating a value', () => {
    const rows = [{ label: 'Version', value: '2' }]
    const { rerender } = render(<SpecSheet title="Spec" rows={rows} visibleCount={Number.NaN} />)
    expect(screen.queryByText('Version')).toBeNull()
    rerender(<SpecSheet title="Spec" rows={rows} visibleCount={-1} />)
    expect(screen.queryByText('Version')).toBeNull()
    rerender(<SpecSheet title="Spec" rows={rows} visibleCount={5} />)
    expect(screen.getAllByText('Version')).toHaveLength(1)
    expect(screen.getByText('2')).toBeTruthy()
  })
})

describe('ScoreBreakdown measured scores', () => {
  const criteria = [
    { label: 'Accuracy', score: 7.5, weight: 2, note: 'Held-out review' },
    { label: 'Coverage', score: 4.5, weight: 1 },
  ]

  it('announces source scores and meter widths as criteria arrive', () => {
    const { rerender } = render(<ScoreBreakdown verdict="Pass" total={7.5} outOf={10}
      criteria={criteria} visibleCount={1} />)
    expect(screen.getAllByText('7.5')).toHaveLength(2)
    expect(screen.getByText('/ 10')).toBeTruthy()
    expect(screen.getByText('Held-out review')).toBeTruthy()
    expect(screen.queryByText('Coverage')).toBeNull()
    const accuracy = screen.getByRole('meter', { name: 'Accuracy score' })
    expect(accuracy.getAttribute('aria-valuenow')).toBe('75')
    expect(accuracy.getAttribute('aria-valuetext')).toBe('7.5 of 10')
    expect((accuracy.firstElementChild as HTMLElement).style.width).toBe('75%')
    rerender(<ScoreBreakdown verdict="Pass" total={7.5} outOf={10} criteria={criteria} visibleCount={2} />)
    expect(screen.getByRole('meter', { name: 'Coverage score' }).getAttribute('aria-valuenow')).toBe('45')
  })

  it('moves verdict treatment across measured thresholds', () => {
    const props = { verdict: 'Measured', outOf: 10, criteria: [], visibleCount: 0 }
    const { rerender } = render(<ScoreBreakdown {...props} total={7.5} />)
    expect(screen.getByText('Measured').className).toContain('bg-emerald-500/12')
    rerender(<ScoreBreakdown {...props} total={5} />)
    expect(screen.getByText('Measured').className).toContain('bg-amber-500/12')
    rerender(<ScoreBreakdown {...props} total={4.9} />)
    expect(screen.getByText('Measured').className).toContain('bg-red-500/12')
  })

  it('bounds invalid meter shares while retaining the recorded score text', () => {
    const { rerender } = render(<ScoreBreakdown verdict="Unknown" total={0} outOf={0}
      criteria={[{ label: 'Unrated', score: 4, weight: 1 }]} visibleCount={1} />)
    const meter = screen.getByRole('meter')
    expect(meter.getAttribute('aria-valuenow')).toBe('0')
    expect(meter.getAttribute('aria-valuetext')).toBe('4.0 of 0')
    rerender(<ScoreBreakdown verdict="Bounded" total={12} outOf={10}
      criteria={[{ label: 'Unrated', score: 12, weight: 1 }]} visibleCount={1} />)
    expect(meter.getAttribute('aria-valuenow')).toBe('100')
    expect((meter.firstElementChild as HTMLElement).style.width).toBe('100%')
    rerender(<ScoreBreakdown verdict="Bounded" total={0} outOf={10}
      criteria={[{ label: 'Unrated', score: -2, weight: 1 }]} visibleCount={1} />)
    expect(meter.getAttribute('aria-valuenow')).toBe('0')
    expect((meter.firstElementChild as HTMLElement).style.width).toBe('0%')
  })
})

describe('NumberTicker real numeric changes', () => {
  it('keeps the accessible number synchronized with each rolling digit', () => {
    const { container, rerender } = render(<NumberTicker value={1234} label="Processed rows" />)
    const value = container.querySelector('[data-slot="number-ticker"] [aria-label]') as HTMLElement
    const tracks = () => Array.from(value.querySelectorAll<HTMLElement>('[style*="translateY"]'))
    expect(value.getAttribute('aria-label')).toBe('1,234')
    const positions = tracks().map((track) => Number.parseFloat(track.style.transform.slice('translateY('.length)))
    expect(positions).toHaveLength(4)
    positions.forEach((position, index) => expect(position).toBeCloseTo([-1.15, -2.3, -3.45, -4.6][index]))
    expect(screen.getByText('Processed rows')).toBeTruthy()
    rerender(<NumberTicker value={1294} label="Processed rows" />)
    expect(value.getAttribute('aria-label')).toBe('1,294')
    expect(Number.parseFloat(tracks()[2].style.transform.slice('translateY('.length))).toBeCloseTo(-10.35)
  })

  it('retains sign and decimal punctuation while rolling only numeric glyphs', () => {
    const { container, rerender } = render(<NumberTicker value={-98.5} label="Delta" />)
    const value = container.querySelector('[data-slot="number-ticker"] [aria-label]') as HTMLElement
    expect(value.getAttribute('aria-label')).toBe('-98.5')
    expect(value.querySelectorAll('[style*="translateY"]')).toHaveLength(3)
    rerender(<NumberTicker value={0} label="Delta" />)
    expect(value.getAttribute('aria-label')).toBe('0')
    expect(value.querySelectorAll('[style*="translateY"]')).toHaveLength(1)
  })
})

describe('MathBlock supplied derivation', () => {
  const steps = [
    { expression: 'rate = distance / time', note: 'Measured inputs' },
    { expression: <><Frac over="distance" under="time" /> <Sup>2</Sup><Sub>n</Sub></>, note: 'Normalized result' },
  ]

  it('reveals an exact mathematical result and its notation one step at a time', () => {
    const { container, rerender } = render(<MathBlock label="Derivation" steps={steps} visibleSteps={1} />)
    expect(screen.getByText('Derivation')).toBeTruthy()
    expect(screen.getByText('rate = distance / time')).toBeTruthy()
    expect(screen.queryByText('Normalized result')).toBeNull()
    rerender(<MathBlock label="Derivation" steps={steps} visibleSteps={2} />)
    expect(screen.getByText('Normalized result')).toBeTruthy()
    const fraction = container.querySelector('[data-slot="frac"]') as HTMLElement
    expect(fraction.textContent).toBe('distancetime')
    expect(fraction.querySelector('span.border-t')?.textContent).toBe('time')
    expect(container.querySelector('sup')?.textContent).toBe('2')
    expect(container.querySelector('sub')?.textContent).toBe('n')
  })

  it('does not expose unfinished or invalidly counted derivation steps', () => {
    const { rerender } = render(<MathBlock steps={steps} visibleSteps={Number.NaN} />)
    expect(screen.queryByText('Measured inputs')).toBeNull()
    rerender(<MathBlock steps={steps} visibleSteps={-1} />)
    expect(screen.queryByText('Measured inputs')).toBeNull()
    rerender(<MathBlock steps={steps} visibleSteps={99} />)
    expect(screen.getByText('Measured inputs')).toBeTruthy()
    expect(screen.getByText('Normalized result')).toBeTruthy()
  })
})
