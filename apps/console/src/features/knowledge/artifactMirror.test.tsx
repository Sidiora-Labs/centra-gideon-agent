import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SourceRow } from './SourcesPage'
import { ARTIFACT_TYPE, isArtifactItem, resolveType, typeLabel, TYPES } from './knowledgeMeta'
import { eventDrivenMetaLine } from './sourceMeta'
import type { WatchedSource } from '../../shared/data/api'


const KINDS = {
  'artifacts': { display_name: 'Artifacts', form: 'artifact' },
  'watched-page': { display_name: 'Watched Page', form: 'web_page' },
}

function source(over: Partial<WatchedSource> = {}): WatchedSource {
  return {
    id: 'src-art', name: 'Artifacts', provider: 'artifacts', kind: 'artifact',
    spec: { uri: 'artifact://' }, budget: {},
    enrichment: 'raw', poll_interval_secs: 0, item_type: 'artifact', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 0,
    last_poll_at: null, enrolled: false, event_driven: true,
    remediation: { kind: '', guidance: '', detail: '', action: '' },
    ...over,
  }
}

function renderRow(over: Partial<WatchedSource> = {}) {
  return render(<SourceRow source={source(over)} kinds={KINDS} onChanged={() => {}} />)
}

describe('an event-driven source is not described as a broken poller', () => {
  it('does NOT claim no provider will collect anything', () => {
    renderRow()

    expect(screen.queryByText('No provider'), 'the mirror is fed by a listener, not a poll').toBeNull()
  })

  it('still shows the danger chip for a genuinely orphaned poller', () => {
    renderRow({ provider: 'watched-page', event_driven: false, enrolled: false })

    expect(screen.getByText('No provider')).toBeTruthy()
  })

  it('does not report a poll health it never earned', () => {
    renderRow()

    expect(screen.queryByText('Not polled yet')).toBeNull()
    expect(screen.queryByText('Healthy')).toBeNull()
  })

  it('says how it IS fed, and where its one switch lives', () => {
    renderRow()

    expect(screen.getByText(eventDrivenMetaLine())).toBeTruthy()
    expect(eventDrivenMetaLine()).toMatch(/Settings → Sources/)
  })

  it('does not open by repeating the row title as a lowercase provider name', () => {
    renderRow()

    expect(eventDrivenMetaLine().startsWith('artifacts')).toBe(false)
    expect(eventDrivenMetaLine().split(' · ')[0]).toBe('Indexed as artifacts change')
  })

  it('offers no pause toggle, because pausing the row would do nothing', () => {
    renderRow()

    expect(screen.queryByLabelText(/Pause Artifacts/)).toBeNull()
    expect(screen.queryByLabelText(/Resume Artifacts/)).toBeNull()
  })

  it('keeps the pause toggle for a real poller', () => {
    renderRow({ name: 'Changelog', provider: 'watched-page', event_driven: false, enrolled: true })

    expect(screen.getByLabelText('Pause Changelog')).toBeTruthy()
  })
})

describe('a mirrored artifact reads as an artifact', () => {
  it('resolves to the artifact type rather than falling through to note', () => {
    expect(resolveType({ item_type: 'artifact' })).toBe(ARTIFACT_TYPE)
    expect(typeLabel({ item_type: 'artifact' })).toBe('Artifact')
  })

  it('is NOT offered by the create picker', () => {
    expect(TYPES.find((t) => t.key === 'artifact')).toBeUndefined()
  })

  it('recognises the mirror from either the vision type or the raw item_type', () => {
    expect(isArtifactItem({ type: 'artifact' })).toBe(true)
    expect(isArtifactItem({ item_type: 'ARTIFACT' })).toBe(true)
    expect(isArtifactItem({ item_type: 'note' })).toBe(false)
    expect(isArtifactItem({})).toBe(false)
  })
})
