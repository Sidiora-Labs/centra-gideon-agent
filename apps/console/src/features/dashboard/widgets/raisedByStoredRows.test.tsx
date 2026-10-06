import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import type { InboxItem } from '../../../shared/data/api'
import { inboxRaisedBy, toLanes } from '../../../shared/data/attentionLanes'

function nativeRows(): InboxItem[] {
  const home = mkdtempSync(resolve(tmpdir(), 'gideon-notice-rows-'))
  const root = resolve(process.cwd(), '../..')
  try {
    return JSON.parse(execFileSync(process.env.GIDEON_TEST_PYTHON || 'python3', ['-m', 'checks.runtime.notice_rows_fixture'], {
      cwd: root, encoding: 'utf8', env: { ...process.env, GIDEON_HOME: home, PYTHONPATH: resolve(root, 'runtime') },
    }))
  } finally { rmSync(home, { recursive: true, force: true }) }
}

afterEach(() => { cleanup(); vi.resetModules(); vi.unstubAllGlobals() })

describe('native persisted attention rows name the work that raised them', () => {
  it('uses the same stored refs in lanes, Home, and Companion', async () => {
    const rows = nativeRows()
    expect(rows).toHaveLength(2)
    expect(rows.map(inboxRaisedBy)).toEqual(['Release Companion', 'Workflow: Release preparation'])
    expect(toLanes(rows, [])['your-turn'].map((card) => card.subtitle)).toEqual(['Release Companion', 'Workflow: Release preparation'])
    vi.doMock('../DashboardLive', () => ({ useDashboardLive: () => ({
      approvals: [], inbox: rows, proposals: [], refreshAll: () => {},
      read: { approvals: true, inbox: true, proposals: true },
    }) }))
    const { ActionCenter } = await import('./ActionCenter')
    render(<ActionCenter sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
    expect(screen.getByText('Release Companion')).toBeInTheDocument()
    expect(screen.getByText('Workflow: Release preparation')).toBeInTheDocument()
    cleanup()
    vi.doMock('../../../shared/data/data', async (original) => ({
      ...await original<Record<string, unknown>>(),
      useQuery: () => ({ data: rows, refresh: () => {} }),
    }))
    vi.doMock('../../companion/useLiveLane', async (original) => ({
      ...await original<Record<string, unknown>>(), useLiveLane: () => {},
    }))
    const { InboxSection } = await import('../../companion/CompanionSections')
    await act(async () => { render(<InboxSection />) })
    expect(screen.getByText('Release Companion')).toBeInTheDocument()
    expect(screen.getByText('Workflow: Release preparation')).toBeInTheDocument()
    expect(screen.queryByText('app:approval-demo')).not.toBeInTheDocument()
  })
})
