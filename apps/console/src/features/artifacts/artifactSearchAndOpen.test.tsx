import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Artifact } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { ArtifactsSection } from './ArtifactsSection'

const { artifacts } = vi.hoisted(() => ({ artifacts: vi.fn() }))

vi.mock('../../shared/data/api', async (original) => {
  const real = await original<typeof import('../../shared/data/api')>()
  return { ...real, api: { ...real.api, artifacts, deployedArtifacts: async () => [] } }
})

vi.mock('./ArtifactGrid', () => ({
  ArtifactGrid: ({ artifacts: rows }: { artifacts: Artifact[] }) => (
    <div>{rows.map((row) => <span key={row.slug}>{row.name}</span>)}</div>
  ),
}))

vi.mock('./ArtifactViewer', () => ({
  ArtifactViewer: ({ onOpenSourceFile }: { onOpenSourceFile: (path: string) => void }) => (
    <button onClick={() => onOpenSourceFile('/workspace/reports/exact.md')}>Open source</button>
  ),
}))

function Harness({ sub = '', navigate = vi.fn() }: { sub?: string; navigate?: RouteProps['navigate'] }) {
  const [query, setQueryState] = useState<Record<string, string>>({})
  const setQuery: RouteProps['setQuery'] = (patch) => setQueryState((current) => {
    const next = { ...current }
    for (const [key, value] of Object.entries(patch)) {
      if (value) next[key] = value
      else delete next[key]
    }
    return next
  })
  return <ArtifactsSection sub={sub} navigate={navigate} navEpoch={0} query={query} setQuery={setQuery} />
}

describe('artifact body search and source-file opening', () => {
  beforeEach(() => {
    artifacts.mockReset()
    artifacts.mockResolvedValue([])
  })

  it('sends the search text to the artifact API so rendered bodies are searched', async () => {
    render(<Harness />)
    await waitFor(() => expect(artifacts).toHaveBeenCalledWith(undefined))

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search artifacts' }), { target: { value: 'body needle' } })

    await waitFor(() => expect(artifacts).toHaveBeenCalledWith({ q: 'body needle' }))
  })

  it('opens the exact displayed source path', async () => {
    const navigate = vi.fn()
    artifacts.mockResolvedValue([{
      slug: 'report', name: 'Report', source_path: '/workspace/reports/exact.md', readonly: false,
      kind: 'markdown', source: 'manual', description: '', tags: [], version: 1,
      created_at: '', updated_at: '', events: [],
    } as Artifact])
    render(<Harness sub="report" navigate={navigate} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open source' }))

    expect(navigate).toHaveBeenCalledWith(
      'files?dir=%2Fworkspace%2Freports&file=%2Fworkspace%2Freports%2Fexact.md',
    )
  })
})
