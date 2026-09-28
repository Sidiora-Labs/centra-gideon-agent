import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import type { Loop } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { loopRoute } from '../../shared/data/loopKind'
import { loopActionSources } from '../../shared/data/loopStatus'
import { loopToGoalLoop } from './goalAdapter'
import { LoopsListPage } from './LoopsListPage'
import { MissionControl } from '../dashboard/MissionControl'
import { toLanes } from '../../shared/data/attentionLanes'

function row(over: Partial<Loop> = {}): Loop {
  return {
    id: 'r1', kind: 'general', name: 'Weekly checklist', task: 'write a three-item checklist',
    execution: 'solo', agent: '', model: '', attended: false, max_cycles: 6, idle_secs: 0,
    success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1780000000, started_at: 1780000005, completed_at: null, kind_config: {},
    findings: [], ...over,
  }
}

function Harness() {
  const [query, setQ] = useState<Record<string, string>>({ filter: 'all' })
  const [destination, setDestination] = useState('')
  const setQuery: RouteProps['setQuery'] = (patch) => setQ((q) => {
    const next = { ...q }
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === '') delete next[key]
      else next[key] = value
    }
    return next
  })
  return <>
    <LoopsListPage onOpen={(loop) => setDestination(loopRoute(loop))} onCreate={() => setDestination('loops')}
      query={query} setQuery={setQuery} />
    <output aria-label="Opened destination">{destination}</output>
  </>
}

beforeEach(() => resetDataStore())

describe('run-backed loops in the real cached list', () => {
  it('lists the run and opens its run detail without inventing findings', () => {
    writeQuery('loops', [loopToGoalLoop(row({ run_id: 'r1' }))])
    const ui = render(<Harness />)
    expect(ui.getByText('Weekly checklist')).toBeInTheDocument()
    expect(ui.queryByText('No loops yet')).toBeNull()
    expect(ui.queryByText('0 fnd')).toBeNull()
    expect(ui.getAllByRole('button', { name: 'Pause' })).toHaveLength(1)
    fireEvent.click(ui.getByRole('button', { name: 'Weekly checklist' }))
    fireEvent.click(ui.getByRole('button', { name: 'Open the run' }))
    expect(ui.getByLabelText('Opened destination').textContent).toBe('workflows/runs/r1')
  })

  it.each([['r1', 0], [undefined, 1]] as const)('keeps failed-row resume tied to its backing %s', (runId, resumes) => {
    writeQuery('loops', [loopToGoalLoop(row({ id: runId ? 'r1' : 'g1', kind: runId ? 'general' : 'goal', run_id: runId, status: 'failed' }))])
    const ui = render(<Harness />)
    expect(ui.getByText('Weekly checklist')).toBeInTheDocument()
    expect(ui.queryAllByRole('button', { name: 'Resume' })).toHaveLength(resumes)
    if (!runId) expect(ui.getByText('0 fnd')).toBeInTheDocument()
  })

  it('routes every loop backing and reserves needs-input decisions for the run page', () => {
    expect(loopRoute(row({ run_id: 'r1' }))).toBe('workflows/runs/r1')
    expect(loopRoute(row({ id: 'g1', kind: 'goal' }))).toBe('loops/g1')
    expect(loopRoute(row({ id: 'c1', kind: 'code' }))).toBe('code/c1')
    expect(loopActionSources({ run_id: 'r1' }).resume.has('needs_input')).toBe(false)
    expect(loopActionSources({ run_id: 'r1' }).resume.has('paused')).toBe(true)
  })
})


it('shows actual run-backed work and its run link in Mission Control', () => {
  const running = row({ run_id: 'r1' })
  const paused = row({ id: 'r2', run_id: 'r2', name: 'Paused checklist', status: 'paused' })
  const waiting = row({ id: 'r3', run_id: 'r3', status: 'needs_input' })
  const ordinary = row({ id: 'legacy' })
  const loops = [running, paused, waiting, ordinary]
  const lanes = toLanes([], [], [], loops)
  expect(lanes.working.map(card => [card.origin, card.id])).toEqual([['workflow', 'r1']])
  expect(lanes.idle.map(card => card.id)).toEqual(['r2'])
  expect(lanes['needs-approval']).toHaveLength(0)
  expect(lanes['your-turn']).toHaveLength(0)
  writeQuery('dashboard:mission-control', { items: [], approvals: [], activity: [], loops })
  const ui = render(<MissionControl />)
  expect(ui.getByText('Weekly checklist')).toBeInTheDocument()
  expect(ui.getByRole('link', { name: 'Open the run: Weekly checklist — running' })).toHaveAttribute('href', '#/workflows/runs/r1')
  expect(ui.getByRole('link', { name: 'Open the run: Paused checklist — paused' })).toHaveAttribute('href', '#/workflows/runs/r2')
  expect(ui.queryByRole('button', { name: /Approve/ })).toBeNull()
})
