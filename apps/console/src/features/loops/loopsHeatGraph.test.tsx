import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { GoalLoop } from '../../shared/data/api'
import { findingHeatData, LoopPeek } from './LoopsListPage'

afterEach(cleanup)

const LOOP: GoalLoop = {
  id: 'loop-1', name: 'Research loop', goal: 'Find cited evidence', sub_goals: [],
  goal_type: 'open_ended', intake_rigor: 'balanced', execution: 'solo',
  agent: 'gideon', model: 'default', attended: false, granularity: 'balanced',
  max_cycles: 3, idle_secs: 60, success_criteria: null,
  status: 'running', total_cycles: 2, error_message: null,
  created_at: Date.now() / 1000, started_at: null, completed_at: null,
}

describe('LoopPeek finding activity', () => {
  it('aggregates actual finding timestamps by UTC calendar day', () => {
    const dayOne = Date.UTC(2026, 0, 1, 23, 59) / 1000
    const dayTwo = Date.UTC(2026, 0, 2, 0, 1) / 1000
    expect(findingHeatData([
      { cycle: 2, ts: dayTwo },
      { cycle: 1, ts: dayOne },
      { cycle: 3, ts: dayTwo + 60 },
    ])).toEqual([
      { date: '2026-01-01', count: 1 },
      { date: '2026-01-02', count: 2 },
    ])
  })

  it('does not create days or zero counts for missing and invalid timestamps', () => {
    expect(findingHeatData(undefined)).toEqual([])
    expect(findingHeatData([
      { cycle: 1 }, { cycle: 2, ts: 0 }, { cycle: 3, ts: -1 },
      { cycle: 4, ts: Number.NaN }, { cycle: 5, ts: Number.POSITIVE_INFINITY },
      { cycle: 6, ts: Number.MAX_VALUE },
    ])).toEqual([])
  })

  it('mounts the real donor graph only for dated findings and keeps the full-loop action', async () => {
    const onOpenFull = vi.fn()
    const day = new Date()
    day.setHours(12, 0, 0, 0)
    const ts = day.getTime() / 1000
    const loop: GoalLoop = { ...LOOP, sub_goals: ['Check sources'], findings: [
      { cycle: 1, ts }, { cycle: 2, ts: ts + 60 },
    ] }
    render(<LoopPeek loop={loop} onOpenFull={onOpenFull} />)
    const graph = screen.getByRole('region', { name: 'Finding activity by UTC day' })
    expect(screen.getByText('Find cited evidence')).toBeTruthy()
    expect(screen.getByText('Check sources')).toBeTruthy()
    expect(within(graph).getByText('Less')).toBeTruthy()
    expect(within(graph).getByText('More')).toBeTruthy()
    expect(graph.classList.contains('overflow-x-auto')).toBe(true)
    expect(graph.querySelector('.min-w-\\[680px\\]')).toBeTruthy()
    expect(graph.getAttribute('tabindex')).toBe('0')
    const cells = graph.querySelectorAll<HTMLElement>('.aspect-square')
    fireEvent.mouseEnter(cells[cells.length - 1])
    expect(await screen.findByText('2 findings')).toBeTruthy()
    expect(screen.queryByText('2 contributions')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Open full loop' }))
    expect(onOpenFull).toHaveBeenCalledOnce()
  })

  it('keeps the latest summary without claiming activity when findings have no dates', () => {
    const loop: GoalLoop = { ...LOOP, findings: [{ cycle: 1, summary: 'The source was checked.' }] }
    render(<LoopPeek loop={loop} onOpenFull={vi.fn()} />)
    expect(screen.getByText('The source was checked.')).toBeTruthy()
    expect(screen.getByText('Latest finding · 1 total')).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Finding activity by UTC day' })).toBeNull()
    expect(screen.queryByText('Less')).toBeNull()
    expect(screen.getByRole('button', { name: 'Open full loop' })).toBeTruthy()
  })
})
