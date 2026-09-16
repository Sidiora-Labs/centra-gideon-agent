import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../shared/data/api'
import { registerContentType } from '../../shared/ui/content/contentTypes'
import { ArtifactViewer } from './ArtifactViewer'


const SLUG = 'verdant-hollow-design-notes'
const BODY = 'Torque tables, page 4.'

let seen: Record<string, unknown> = {}

vi.mock('../../shared/ui/content/ContentSurface', () => ({
  ContentSurface: (props: Record<string, unknown>) => {
    seen = props
    return <div data-testid="surface">{String(props.content ?? '')}</div>
  },
}))

let selectedVersion: number | null = null

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Verdant Hollow design notes', kind: 'draftprobe', source: 'chat',
    description: '', tags: [], version: 3, content: BODY,
    created_at: '2026-08-20T00:00:00Z', updated_at: '2026-08-20T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => fixture(),
      artifactVersions: async () => ({ slug: SLUG, versions: [1, 2, 3] }),
      artifactEvents: async () => ({ slug: SLUG, events: [] }),
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

registerContentType({
  id: 'draftprobe', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['draftprobe'],
  preview: { render: ({ content }: { content: string }) => <div>{content}</div> },
  commentable: false,
})

beforeEach(() => {
  seen = {}
  selectedVersion = null
})

async function mountViewer() {
  const view = render(
    <ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={vi.fn()}
      onDeleted={() => {}} onOpenSourceFile={() => {}} />,
  )
  await waitFor(() => expect(screen.queryByTestId('surface')).not.toBeNull())
  return view
}

describe('the artifact viewer hands ContentSurface a draft store', () => {
  it('passes a store, so an unsaved edit is not dropped on unmount', async () => {
    await mountViewer()

    expect(seen.draftStore).toBeInstanceOf(Map)
  })

  it('keys the store by the artifact slug, matching the docId the surface reads', async () => {
    await mountViewer()

    expect(seen.docId).toBe(SLUG)
  })

  it('survives the component being unmounted and remounted', async () => {
    const first = await mountViewer()
    const store = seen.draftStore as Map<string, { draft: string; base: string }>
    store.set(SLUG, { draft: 'Torque tables, page 4. Reconciled.', base: BODY })

    first.unmount()
    seen = {}
    await mountViewer()

    const after = seen.draftStore as Map<string, { draft: string; base: string }>
    expect(after.get(SLUG)?.draft).toBe('Torque tables, page 4. Reconciled.')
  })

  it('withholds the store on a read-only view, so a draft cannot leak into it', async () => {
    await mountViewer()
    const select = screen.getByRole('combobox', { name: 'Version' })
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(select, { target: { value: '1' } })

    await waitFor(() => expect(seen.readOnly).toBe(true))
    expect(seen.draftStore).toBeUndefined()
    void selectedVersion
  })
})
