import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { FeedbackProducerRow } from '../../shared/data/api'
import { FeedbackPanel } from './FeedbackPanel'


const feedbackProducers = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    feedbackProducers: (...a: unknown[]) => feedbackProducers(...a),
    feedbackSnooze: vi.fn(),
    feedbackClear: vi.fn(),
  },
}))
vi.mock('../../app/shell/appSdk', () => ({ notify: vi.fn() }))

const ROWS: FeedbackProducerRow[] = [
  { producer_kind: 'prompt', producer_id: 'task-inbox-classify', ups: 3, downs: 6, n: 9, accuracy: 0.333, proposal_only: true },
  { producer_kind: 'prompt', producer_id: 'task-inbox-draft', ups: 7, downs: 0, n: 7, accuracy: 1, suppressed: false },
]

describe('a feedback count says which way it counts', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    feedbackProducers.mockResolvedValue({ producers: ROWS, min_n: 5, window_days: 90 })
  })

  it('every count is a named image, never a bare number', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.getByRole('img', { name: '3 marked accurate' })).toBeTruthy()
    expect(screen.getByRole('img', { name: '6 marked wrong' })).toBeTruthy()
    expect(screen.getByRole('img', { name: '7 marked accurate' })).toBeTruthy()
    expect(screen.getByRole('img', { name: '0 marked wrong' })).toBeTruthy()
  })

  it('the two counts in a row never share a name', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    const names = screen.getAllByRole('img').map((el) => el.getAttribute('aria-label'))
    expect(new Set(names).size, `duplicate count names would re-create the ambiguity:\n${names.join('\n')}`)
      .toBe(names.length)
  })

  it('the visible text is unchanged — this is a tree fix, not a redesign', async () => {
    render(<FeedbackPanel />)
    await waitFor(() => expect(screen.getByText('task-inbox-classify')).toBeTruthy())
    expect(screen.getByText('3')).toBeTruthy()
    expect(screen.getByText('6')).toBeTruthy()
  })

  it('the row keeps a floor for its identity, and may wrap instead of starving it', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/FeedbackPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const rowAt = src.indexOf('<div className="flex flex-wrap items-center gap-3 rounded-lg bg-surface-container')
    expect(rowAt, 'the producer row must be allowed to wrap').toBeGreaterThan(-1)
    const identity = src.slice(rowAt, rowAt + 400)
    expect(identity, 'and the identity block needs a floor, not permission to vanish')
      .toMatch(/<div className="min-w-40 flex-1">/)
    expect(identity, 'min-w-0 there is what let the pills paint over the name').not.toMatch(/min-w-0 flex-1/)
  })

  it('the summary speaks the same vocabulary as the control that produces it', () => {
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const thumbs = strip(readFileSync(join(process.cwd(), "src/shared/ui/FeedbackThumbs.tsx"), 'utf8'))
    const panel = strip(readFileSync(join(process.cwd(), "src/features/settings/FeedbackPanel.tsx"), 'utf8'))
    expect(thumbs, 'the control names the up verdict "accurate"').toMatch(/Mark accurate/)
    expect(thumbs, 'and the down verdict "wrong"').toMatch(/Mark wrong/)
    expect(panel, 'so the summary counts say accurate').toMatch(/marked accurate/)
    expect(panel, 'and wrong').toMatch(/marked wrong/)
  })
})
