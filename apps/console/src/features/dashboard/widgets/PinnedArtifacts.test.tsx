import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Artifact, PinnedArtifact } from '../../../shared/data/api'
import type { RouteProps } from '../../../app/shell/useQueryState'
import { PinnedArtifacts } from './PinnedArtifacts'


let pins: PinnedArtifact[]
let artifacts: Artifact[]
const unpinned: string[] = []

vi.mock('../../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      pinnedArtifacts: async () => ({ pins }),
      artifacts: async () => artifacts,
      pinArtifact: async (slug: string, pinned: boolean) => {
        if (!pinned) unpinned.push(slug)
        return { ok: true, pinned, pins }
      },
    },
  }
})

function art(over: Partial<Artifact> = {}): Artifact {
  return {
    slug: 'report', name: 'Weekly report', kind: 'markdown', source: 'workflow',
    description: '', tags: [], version: 3, created_at: '', updated_at: '',
    events: [], source_path: '', live_dirty: false, mime: '', project_id: '', collection: '',
    ...over,
  } as Artifact
}

const route: RouteProps = {
  sub: '', navigate: () => {}, navEpoch: 0, query: {}, setQuery: () => {},
}

beforeEach(() => {
  pins = [{ slug: 'report', pinned_at: '2026-08-11T02:00:00+00:00', run_id: 'r1' }]
  artifacts = [art()]
  unpinned.length = 0
})

describe('a pin renders the artifact, resolved at load', () => {
  it('shows the CURRENT name and version, not a stored copy', async () => {
    artifacts = [art({ name: 'Renamed report', version: 7 })]
    render(<PinnedArtifacts {...route} />)
    expect(await screen.findByText('Renamed report')).toBeTruthy()
    expect(screen.getByText(/v7/)).toBeTruthy()
  })

  it('omits the version suffix for a v1 artifact', async () => {
    artifacts = [art({ version: 1 })]
    render(<PinnedArtifacts {...route} />)
    await screen.findByText('Weekly report')
    expect(screen.queryByText(/v1/)).toBeNull()
  })
})

describe('a pin whose artifact is gone', () => {
  it('drops off the surface rather than rendering a dead card', async () => {
    artifacts = []
    render(<PinnedArtifacts {...route} />)
    expect(await screen.findByText(/no pinned artifacts/i)).toBeTruthy()
  })

  it('does NOT delete the pin from the store', async () => {
    artifacts = []
    render(<PinnedArtifacts {...route} />)
    await screen.findByText(/no pinned artifacts/i)
    expect(unpinned).toEqual([])
  })
})

describe('unpinning', () => {
  it('removes the row and writes through', async () => {
    render(<PinnedArtifacts {...route} />)
    fireEvent.click(await screen.findByTitle('Unpin'))
    await waitFor(() => expect(unpinned).toEqual(['report']))
    expect(screen.queryByText('Weekly report')).toBeNull()
  })
})

describe('the empty state', () => {
  it('says how to get here rather than just being blank', async () => {
    pins = []
    render(<PinnedArtifacts {...route} />)
    expect(await screen.findByText(/pin one from its page/i)).toBeTruthy()
  })

  it('renders nothing at all before the first load resolves', () => {
    const { container } = render(<PinnedArtifacts {...route} />)
    expect(container.textContent).toBe('')
  })
})
