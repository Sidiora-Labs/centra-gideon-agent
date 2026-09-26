import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import type { TelemetryRow, VoiceProfile } from '../data/api'
import { Table, THead, Th, Td } from './Table'
import { UsageTable } from '../../features/settings/UsagePanel'
import { TelemetryTable } from '../../features/settings/RoutingPanel'
import { ProfileRow } from '../../features/settings/VoiceProfilesSection'

afterEach(cleanup)

const longRef = 'provider/model-with-a-very-long-unbroken-identifier-for-narrow-settings'

function expectConfined(table: HTMLElement, caption: string) {
  expect(table.getAttribute('aria-label')).toBeNull()
  expect(table.querySelector('caption')?.textContent).toBe(caption)
  const surface = table.parentElement!
  expect(surface.getAttribute('data-table-surface')).not.toBeNull()
  expect(surface.getAttribute('role')).toBe('region')
  expect(surface.getAttribute('aria-label')).toBe(caption)
  expect(surface.getAttribute('tabindex')).toBe('0')
  for (const token of ['w-full', 'min-w-0', 'max-w-full', 'overflow-x-auto', 'overscroll-x-contain']) {
    expect(surface.classList.contains(token)).toBe(true)
  }
  expect(table.classList.contains('min-w-full')).toBe(true)
  expect(table.classList.contains('w-max')).toBe(true)
}

describe('narrow settings tables', () => {
  it('keeps every column and a live row action inside the scroll region at a 390px parent width', () => {
    function Actions() {
      const [done, setDone] = useState(false)
      return <div style={{ width: 390 }}>
        <Table caption="Settings actions" wrapClassName="settings-table">
          <THead><tr><Th>Model</Th><Th>Tokens</Th><Th>Cost</Th><Th align="right">Actions</Th></tr></THead>
          <tbody><tr><Td>{longRef}</Td><Td>12,000</Td><Td>$2.50</Td>
            <Td align="right"><button onClick={() => setDone(true)}>Inspect model</button></Td></tr></tbody>
        </Table>
        <span>{done ? 'Inspected' : 'Ready'}</span>
      </div>
    }
    render(<Actions />)
    const table = screen.getByRole('table', { name: 'Settings actions' })
    expectConfined(table, 'Settings actions')
    expect(table.parentElement?.classList.contains('settings-table')).toBe(true)
    expect(within(table).getAllByRole('columnheader')).toHaveLength(4)
    expect(within(table).getAllByRole('cell')).toHaveLength(4)
    expect(within(table).getByText(longRef)).toBeTruthy()
    fireEvent.click(within(table).getByRole('button', { name: 'Inspect model' }))
    expect(screen.getByText('Inspected')).toBeTruthy()
  })

  it('keeps the actual usage model, totals and share in a bounded table', () => {
    render(<div style={{ width: 390 }}><UsageTable keyField="model" empty="No usage" rows={[{
      model: longRef, input_tokens: 12000, output_tokens: 400,
      cache_read_tokens: 0, cache_creation_tokens: 0, cost_usd: 2.5, turns: 3, priced: true,
    }]} /></div>)
    const table = screen.getByRole('table', { name: 'Token usage and cost per model' })
    expectConfined(table, 'Token usage and cost per model')
    expect(within(table).getAllByRole('columnheader')).toHaveLength(4)
    expect(within(table).getAllByRole('cell')).toHaveLength(4)
    const model = within(table).getByRole('cell', { name: longRef })
    expect(model.classList.contains('break-all')).toBe(true)
    expect(model.classList.contains('max-w-64')).toBe(true)
    expect(within(table).getByText('100%')).toBeTruthy()
  })

  it('keeps all eight real routing telemetry columns and the long model reference', () => {
    const row: TelemetryRow = {
      ref: longRef, n: 4, success: 0.75, feedback: 0.5,
      p50_ms: 100, p95_ms: 400, avg_cost_usd: 0.012, on_frontier: true,
    }
    render(<div style={{ width: 390 }}><TelemetryTable rows={[row]} /></div>)
    const table = screen.getByRole('table', { name: 'Routing telemetry' })
    expectConfined(table, 'Routing telemetry')
    expect(within(table).getAllByRole('columnheader')).toHaveLength(8)
    expect(within(table).getAllByRole('cell')).toHaveLength(8)
    expect(within(table).getByRole('cell', { name: longRef }).classList.contains('break-all')).toBe(true)
    expect(within(table).getByText('4')).toBeTruthy()
  })

  it('keeps a real voice profile row and both accessible actions without truncating its identity', () => {
    const profile: VoiceProfile = {
      id: 'vp-a1b2c3d4', name: longRef, kind: 'design', provider: 'piper', model: longRef,
      ref_audio: '', ref_text: '', design_params: {}, instruct: '', seed: 0,
      language: '', speed: 1, locked: false, locked_at: '',
      verified_own_voice: false, consent_text: '', consent_audio: '', consent_recorded_at: '',
      history: [], created_at: '', updated_at: '', artifacts: {}, history_count: 1,
    }
    const view = render(<div style={{ width: 390 }}><Table caption="Voice profiles"><tbody>
      <ProfileRow profile={profile} busy="" run={async (_label, task) => { await task() }} />
    </tbody></Table></div>)
    const table = screen.getByRole('table', { name: 'Voice profiles' })
    expectConfined(table, 'Voice profiles')
    expect(within(table).getAllByRole('cell')).toHaveLength(5)
    expect(within(table).getByText(longRef)).toBeTruthy()
    expect(within(table).getAllByRole('cell')[2].textContent).toContain(longRef)
    expect(within(table).getByRole('button', { name: /Lock .* to its latest generation/ })).toBeTruthy()
    expect(within(table).getByRole('button', { name: /Delete/ })).toBeTruthy()
    expect(within(table).getAllByRole('cell')[0].classList.contains('break-all')).toBe(true)
    view.rerender(<div style={{ width: 390 }}><Table caption="Voice profiles"><tbody>
      <ProfileRow profile={{ ...profile, provider: '', model: '' }}  busy="" run={async (_label, task) => { await task() }} />
    </tbody></Table></div>)
    expect(screen.getByText('Bound model')).toBeTruthy()
  })
})
