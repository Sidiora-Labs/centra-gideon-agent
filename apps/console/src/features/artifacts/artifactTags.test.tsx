import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Artifact, ArtifactUpdate } from '../../shared/data/api'
import { ArtifactViewer } from './ArtifactViewer'

const SLUG = 'tagged-brief'
let artifact: Artifact
const updateArtifact = vi.fn<(slug: string, body: ArtifactUpdate) => Promise<Artifact>>()

vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../shared/ui/content/ContentSurface', () => ({
  ContentSurface: () => <div data-testid="artifact-content" />,
}))
vi.mock('../../shared/data/api', async (original) => {
  const real = await original<typeof import('../../shared/data/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => artifact,
      artifactVersions: async () => ({ slug: SLUG, versions: [1] }),
      artifactEvents: async () => ({ slug: SLUG, events: [] }),
      updateArtifact,
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

function fixture(overrides: Partial<Artifact> = {}): Artifact {
  return {
    slug: SLUG,
    name: 'Tagged brief',
    kind: 'markdown',
    source: 'manual',
    description: '',
    tags: ['draft'],
    version: 1,
    created_at: '2026-09-19T00:00:00Z',
    updated_at: '2026-09-19T00:00:00Z',
    content: '# Brief',
    events: [],
    source_path: '',
    readonly: false,
    ...overrides,
  }
}

async function mountViewer(onChanged = vi.fn()) {
  render(<ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={onChanged}
    onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  await screen.findByTestId('artifact-content')
  return onChanged
}

beforeEach(() => {
  artifact = fixture()
  updateArtifact.mockReset()
  updateArtifact.mockImplementation(async (_slug, body) => {
    artifact = { ...artifact, ...body }
    return artifact
  })
})

describe('artifact tags', () => {
  it('adds and removes pills through the shared chip editor and persists each edit', async () => {
    const onChanged = await mountViewer()
    const input = screen.getByRole('textbox', { name: 'Artifact tags' })

    fireEvent.change(input, { target: { value: 'review' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(updateArtifact).toHaveBeenCalledWith(SLUG, { tags: ['draft', 'review'] }))
    expect(screen.getByRole('button', { name: 'Remove review' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Remove draft' }))
    await waitFor(() => expect(updateArtifact).toHaveBeenLastCalledWith(SLUG, { tags: ['review'] }))
    expect(onChanged).toHaveBeenCalledTimes(2)
  })

  it('restores the pills when persistence fails', async () => {
    updateArtifact.mockRejectedValueOnce(new Error('offline'))
    await mountViewer()
    const input = screen.getByRole('textbox', { name: 'Artifact tags' })

    fireEvent.change(input, { target: { value: 'review' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove review' })).toBeNull())
    expect(screen.getByRole('button', { name: 'Remove draft' })).toBeTruthy()
  })

  it('keeps read-only artifact tags as non-editable pills', async () => {
    artifact = fixture({ readonly: true })
    await mountViewer()

    expect(screen.queryByRole('textbox', { name: 'Artifact tags' })).toBeNull()
    expect(screen.getByText('draft')).toBeTruthy()
  })
})
