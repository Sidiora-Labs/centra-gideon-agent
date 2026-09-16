import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact, ArtifactEvent } from '../../shared/data/api'
import type { WsMessage } from '../../shared/data/useChatSocket'
import { registerContentType } from '../../shared/ui/content/contentTypes'
import { isArtifactUpdateFor } from './artifactUpdateSignal'
import { ArtifactViewer } from './ArtifactViewer'


const SLUG = 'revenue-widget'
const V2_BODY = 'chart: revenue only'
const V3_BODY = 'chart: revenue AND margin'

let onMessage: ((m: WsMessage) => void) | null = null
vi.mock('../../shared/data/useChatSocket', () => ({
  useChatSocket: (cb: (m: WsMessage) => void) => { onMessage = cb },
}))

let current = { version: 2, content: V2_BODY }
let versions: number[] = [1, 2]
let events: ArtifactEvent[] = []
let versionsFail = ''
let eventsFail = ''
const fetches = { artifact: 0 }

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Revenue widget', kind: 'ae10probe', source: 'chat',
    description: '', tags: [], version: current.version, content: current.content,
    created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => { fetches.artifact++; return fixture() },
      artifactVersions: async () => {
        if (versionsFail) throw new Error(versionsFail)
        return { slug: SLUG, versions }
      },
      artifactEvents: async () => {
        if (eventsFail) throw new Error(eventsFail)
        return { slug: SLUG, events }
      },
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

function ProbePreview({ content }: { content: string }) {
  return <div data-testid="preview">{content}</div>
}

registerContentType({
  id: 'ae10probe', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['ae10probe'],
  preview: { render: ProbePreview },
  commentable: false,
})

beforeEach(() => {
  onMessage = null
  current = { version: 2, content: V2_BODY }
  versions = [1, 2]
  events = [{ type: 'created', version: 1, ts: '2026-08-16T00:00:00Z', by: 'agent' } as unknown as ArtifactEvent]
  versionsFail = ''
  eventsFail = ''
  fetches.artifact = 0
})

function railLabels(): string[] {
  const sel = screen.queryByRole('combobox', { name: 'Version' })
  if (!sel) return []
  return [...sel.querySelectorAll('option')].map((o) => o.textContent ?? '')
}

function frame(data: Record<string, unknown>): WsMessage {
  return { type: 'tool_call', data: { session: 'sess-1', ...data } }
}

async function mountViewer(onChanged = vi.fn()) {
  render(<ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={onChanged}
    onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
  return onChanged
}

describe('an artifact_update frame refreshes the open detail view', () => {
  it('grows the version rail N→N+1 and repaints the preview in place', async () => {
    const onChanged = await mountViewer()

    const before = railLabels()
    expect(before, 'the rail must be non-empty BEFORE the frame').toEqual(['Current · v2', 'v1'])
    expect(before.length).toBeGreaterThan(1)
    const paintedBefore = screen.getByTestId('preview')
    expect(paintedBefore.textContent).toBe(V2_BODY)
    const fetchesBefore = fetches.artifact

    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { onMessage!(frame({ tool: 'artifact_update', input: { slug: SLUG, content: V3_BODY } })) })

    const after = railLabels()
    expect(after.length).toBe(before.length + 1)
    expect(after).toEqual(['Current · v3', 'v2', 'v1'])
    expect(fetches.artifact).toBe(fetchesBefore + 1)

    const paintedAfter = screen.getByTestId('preview')
    expect(paintedAfter.textContent).toBe(V3_BODY)
    expect(paintedAfter, 'the preview repainted in place, not by remounting').toBe(paintedBefore)

    expect(onChanged).toHaveBeenCalled()

    current = { version: 4, content: 'chart: revenue, margin AND headcount' }
    versions = [1, 2, 3, 4]
    await act(async () => { onMessage!(frame({ tool: 'artifact_update', input: { slug: SLUG } })) })
    expect(railLabels()).toEqual(['Current · v4', 'v3', 'v2', 'v1'])
  })

  it('ignores an artifact_update for a DIFFERENT slug', async () => {
    await mountViewer()
    const before = railLabels()
    expect(before).toEqual(['Current · v2', 'v1'])

    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { onMessage!(frame({ tool: 'artifact_update', input: { slug: 'someone-elses-widget' } })) })

    expect(railLabels()).toEqual(['Current · v2', 'v1'])
    expect(screen.getByTestId('preview').textContent).toBe(V2_BODY)
  })

  it('ignores a tool call that is not artifact_update', async () => {
    await mountViewer()
    expect(railLabels()).toEqual(['Current · v2', 'v1'])

    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { onMessage!(frame({ tool: 'artifact_save', input: { slug: SLUG } })) })

    expect(railLabels()).toEqual(['Current · v2', 'v1'])
    expect(screen.getByTestId('preview').textContent).toBe(V2_BODY)
  })
})

describe('a failed side-fetch is an error, never an empty state', () => {
  it('says the version history could not be LOADED (and offers no picker)', async () => {
    versionsFail = 'HTTP 503 versions'
    await mountViewer()

    expect(screen.getByText(/Couldn't load version history/)).toBeTruthy()
    expect(screen.getByText(/HTTP 503 versions/)).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Version' })).toBeNull()
    expect(screen.queryByText('No version history.')).toBeNull()
  })

  it('says there is NO version history when the fetch succeeds and is empty', async () => {
    versions = []
    await mountViewer()

    expect(screen.getByText('No version history.')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load version history/)).toBeNull()
  })

  it('says the timeline could not be LOADED', async () => {
    eventsFail = 'HTTP 503 events'
    await mountViewer()
    expect(screen.getByText(/Couldn't load the timeline/)).toBeTruthy()
    expect(screen.queryByText('No events.')).toBeNull()
  })

  it('says there are NO events when the fetch succeeds and is empty', async () => {
    events = []
    await mountViewer()
    expect(screen.getByText('No events.')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the timeline/)).toBeNull()
  })

  it('clears the error once a retry succeeds', async () => {
    versionsFail = 'HTTP 503 versions'
    await mountViewer()
    expect(screen.getByText(/Couldn't load version history/)).toBeTruthy()

    versionsFail = ''
    await act(async () => { onMessage!(frame({ tool: 'artifact_update', input: { slug: SLUG } })) })
    expect(screen.queryByText(/Couldn't load version history/)).toBeNull()
    expect(railLabels()).toEqual(['Current · v2', 'v1'])
  })
})

describe('isArtifactUpdateFor — the filter, with no new WS event behind it', () => {
  const f = (data: Record<string, unknown>): WsMessage => ({ type: 'tool_call', data })

  it('accepts artifact_update on this slug (native structured input)', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input: { slug: SLUG } }), SLUG)).toBe(true)
  })

  it('reads the slug off the `update: True` frame, which carries it on input_preview', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', update: true, input_preview: { slug: SLUG } }), SLUG)).toBe(true)
  })

  it('rejects another tool', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_save', input: { slug: SLUG } }), SLUG)).toBe(false)
  })

  it('rejects another slug', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input: { slug: 'other' } }), SLUG)).toBe(false)
  })

  it('rejects any envelope that is not a tool_call', () => {
    expect(isArtifactUpdateFor({ type: 'chat_chunk', data: { tool: 'artifact_update' } }, SLUG)).toBe(false)
  })

  it('finds the slug inside a stringified arg blob (ACP providers)', () => {
    const blob = `{"slug": "${SLUG}", "content": "…"}`
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input_preview: blob }), SLUG)).toBe(true)
  })

  it('does not let a slug PREFIX match (sales-dash vs sales-dashboard)', () => {
    const blob = '{"slug": "sales-dashboard"}'
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input_preview: blob }), 'sales-dash')).toBe(false)
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input_preview: blob }), 'sales-dashboard')).toBe(true)
  })

  it('fails OPEN on a frame that names no slug at all', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update' }), SLUG)).toBe(true)
  })

  it('never matches without a slug to match against', () => {
    expect(isArtifactUpdateFor(f({ tool: 'artifact_update', input: { slug: SLUG } }), '')).toBe(false)
  })
})
