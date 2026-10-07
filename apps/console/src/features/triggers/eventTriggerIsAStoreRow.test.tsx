import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { resetDataStore, writeQuery } from '../../shared/data/data'
import type { Trigger as WireTrigger } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { TriggersListPage } from './TriggersListPage'
import { resolveOpenTrigger, storeToTrigger } from './triggerMeta'

function eventRow(over: Partial<WireTrigger> = {}): WireTrigger {
  return {
    kind: 'store', store_kind: 'event', id: 'store:event:acme-watch', raw_id: 'event:acme-watch',
    name: 'Acme watch', enabled: true,
    spec: { source: 'memory', pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*' },
    action: { provider: 'notify', config: { title_template: 'Acme changed: $key' } },
    health: 'ok', state: 'active', run_count: 1, last_error: '', broken: [], warnings: [],
    ...over,
  }
}

function manualRow(): WireTrigger {
  return {
    kind: 'store', store_kind: 'manual', id: 'store:manual:tidy', raw_id: 'manual:tidy', name: 'Tidy downloads',
    enabled: true, spec: {}, action: { provider: 'notify', config: {} }, health: 'ok',
    state: 'active', run_count: 0, last_run_ts: Date.now() / 1000 - 300, last_run_status: 'success',
  }
}

function Harness({ initialQuery = {} }: { initialQuery?: Record<string, string> }) {
  const [query, setQ] = useState(initialQuery)
  const setQuery: RouteProps['setQuery'] = (patch) => setQ((q) => {
    const next = { ...q }
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === '') delete next[key]
      else next[key] = value
    }
    return next
  })
  return <TriggersListPage onCreate={() => setQ({ create: '1' })} query={query} setQuery={setQuery} />
}

function seed(rows: WireTrigger[]) {
  writeQuery('triggers:schedules', { jobs: [], unreadable: [] })
  writeQuery('triggers:hooks', [])
  writeQuery('triggers:review', [])
  writeQuery('triggers:store', rows)
  writeQuery('triggers:action-providers', [])
}

beforeEach(() => resetDataStore())

describe('an event trigger in the real canonical cached list', () => {
  it('lists under Data events by its stored pattern without a second event feed', () => {
    seed([eventRow(), manualRow()])
    const ui = render(<Harness initialQuery={{ filter: 'event' }} />)
    expect(ui.getByText('Acme watch')).toBeInTheDocument()
    expect(ui.getByText('Memory write to a key')).toBeInTheDocument()
    expect(ui.queryByText('Tidy downloads')).toBeNull()
    expect(ui.queryByText('On an event')).toBeNull()
  })

  it.each(['store:event:acme-watch', 'event:acme-watch'])('opens canonical and notification identity %s in its native inspector', (open) => {
    seed([eventRow()])
    const ui = render(<Harness initialQuery={{ open }} />)
    expect(ui.getByText('When it runs')).toBeInTheDocument()
    expect(ui.getByText('project.acme.*')).toBeInTheDocument()
    expect(ui.getByRole('button', { name: /Run now/ })).toBeInTheDocument()
    expect(ui.getByText('What it runs')).toBeInTheDocument()
    fireEvent.click(ui.getByRole('button', { name: 'Edit event' }))
    expect(ui.getByDisplayValue('project.acme.*')).toBeInTheDocument()
    expect(ui.getByRole('button', { name: 'Save changes' })).toBeInTheDocument()
  })

  it('keeps manual last-run facts and does not claim to fire on its own', () => {
    seed([manualRow()])
    const ui = render(<Harness initialQuery={{ open: 'store:manual:tidy' }} />)
    expect(ui.getByText('5m ago', { exact: false })).toBeInTheDocument()
    expect(ui.getByText('Only when you run it')).toBeInTheDocument()
    expect(ui.getByText('Runs only when you run it — it never fires on its own')).toBeInTheDocument()
    expect(ui.queryByRole('switch', { name: 'Enabled' })).toBeNull()
  })

  it('names an unknown stored pattern honestly and keeps bare ids out of lifecycle hooks', () => {
    const event = storeToTrigger(eventRow({ spec: { pattern: 'BeforeToolCall' } }))
    expect(event.whenLabel).toBe('Data event · BeforeToolCall')
    expect(resolveOpenTrigger([event], event.rawId)).toBe(event)
    expect(resolveOpenTrigger([{ ...event, kind: 'lifecycle' }], event.rawId)).toBeNull()
  })
})
