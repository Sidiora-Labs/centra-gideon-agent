import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Artifact } from '../../shared/data/api'
import { ArtifactCard, IFRAME_CAP } from './ArtifactCard'
import { ArtifactCard as DonorArtifactCard } from '../../shared/vendor/assistant-ui/elements/artifact-card'
import { PdfFilePreview, ImageFilePreview } from '../../shared/ui/content/renderers'
import { registerBuiltinContentTypes } from '../../shared/ui/content/registerBuiltins'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import { ARTIFACT_KINDS, artifactKindMeta, relTime } from '../files/fileMeta'


registerBuiltinContentTypes()

const fetched: string[] = []

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      artifact: async (slug: string) => {
        fetched.push(slug)
        return { slug, content: `/api/artifacts/${slug}/raw` } as unknown as Artifact
      },
    },
  }
})

const art = (kind: Artifact['kind'], slug = `a-${kind}`): Artifact => ({
  slug, name: `Quarterly report.${kind}`, kind, source: 'chat', version: 1,
  created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
  description: '', tags: [], events: [], source_path: '', readonly: false,
})

function previewPane(): HTMLElement {
  const tile = screen.getByRole('button')
  return tile.querySelector('[data-slot="artifact-preview"]') as HTMLElement
}

beforeEach(() => { fetched.length = 0 })

describe('the donor artifact surface in the live grid', () => {
  it('keeps the donor compact card readable when used outside the grid', () => {
    const { container } = render(<DonorArtifactCard title="Release notes" meta="Markdown · v2" />)
    const card = container.querySelector('[data-slot="artifact-card"]')!
    expect(card).toHaveClass('max-w-xs')
    expect(card).toHaveTextContent('Release notes')
    expect(card).toHaveTextContent('Markdown · v2')
    expect(container.querySelector('[data-slot="artifact-preview"]')).toBeNull()
  })

  it('uses one keyboard-accessible open control with recorded name, kind, version, collection and time', async () => {
    const user = userEvent.setup()
    const record = { ...art('image', 'a-chart'), version: 4, collection: 'Campaign' }
    const opened: Artifact[] = []
    const { container } = render(<ArtifactCard art={record} onOpen={value => opened.push(value)} />)
    const button = screen.getByRole('button', { name: record.name })
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(button).toHaveAttribute('title', record.name)
    const donor = container.querySelector('[data-slot="artifact-card"]')!
    expect(donor).not.toHaveClass('max-w-xs')
    expect(donor).toHaveTextContent(`${artifactKindMeta(record.kind).label} · v4`)
    expect(donor).toHaveTextContent('Campaign')
    expect(donor).toHaveTextContent(relTime(record.updated_at))
    expect(donor.querySelectorAll('img')).toHaveLength(1)
    button.focus()
    await user.keyboard('{Enter}')
    expect(opened).toEqual([record])
    await user.click(button)
    expect(opened).toEqual([record, record])
  })

  it('keeps the donor generating state in its original compact layout', () => {
    render(<DonorArtifactCard title="Draft" meta="ignored" generating words={42} />)
    expect(screen.getByText('Writing')).toBeInTheDocument()
    expect(screen.getByText('42 words')).toBeInTheDocument()
    expect(screen.queryByText('ignored')).toBeNull()
  })

  it('shows recorded source changes immediately and falls back to creation time', () => {
    const record = { ...art('docx', 'a-dirty'), updated_at: '', live_dirty: true }
    const { container } = render(<ArtifactCard art={record} onOpen={() => {}} />)
    const donor = container.querySelector('[data-slot="artifact-card"]')!
    expect(donor).toHaveTextContent('source changed')
    expect(donor.querySelector('[title="The source file changed since the last snapshot"]')).toBeInTheDocument()
    expect(donor).toHaveTextContent(relTime(record.created_at))
    expect(donor).not.toHaveTextContent('undefined')
  })

  it('keeps lazy live previews sandboxed and demotes the oldest above the iframe cap', async () => {
    const records = Array.from({ length: IFRAME_CAP + 1 }, (_, index) => ({
      ...art('html', `a-live-${index}`), name: `Live ${index}`,
    }))
    render(<div>{records.map(record => <ArtifactCard key={record.slug} art={record} onOpen={() => {}} />)}</div>)
    await waitFor(() => expect(fetched).toHaveLength(IFRAME_CAP + 1))
    await waitFor(() => expect(document.querySelectorAll('iframe[title^="Preview of Live"]')).toHaveLength(IFRAME_CAP))
    expect(screen.getByRole('button', { name: 'Live 0' }).querySelector('iframe')).toBeNull()
    expect(screen.getByRole('button', { name: `Live ${IFRAME_CAP}` }).querySelector('iframe')).toBeInTheDocument()
    for (const frame of document.querySelectorAll('iframe[title^="Preview of Live"]')) {
      expect(frame).toHaveAttribute('sandbox', 'allow-scripts')
      expect(frame.closest('[data-slot="artifact-preview"]')).toBeInTheDocument()
    }
  })
})

describe('a generated document is labelled by its real kind', () => {
  it('names the format instead of falling through to "Widget"', () => {
    const km = artifactKindMeta('docx')
    expect(km.label, 'a Word document read "Widget" from v0.1.0 to 0.1.3').not.toBe('Widget')
    expect(km.label).toMatch(/word/i)
  })

  it('renders that label on the card, not the fallback kind', () => {
    render(<ArtifactCard art={art('docx')} onOpen={() => {}} />)
    expect(screen.getByText(`${artifactKindMeta('docx').label} · v1`)).toBeInTheDocument()
    expect(screen.queryByText('Widget')).toBeNull()
  })

  it('is selectable in the library toolbar filter', () => {
    const keys = ARTIFACT_KINDS.map((k) => k.key)
    for (const kind of ['docx', 'xlsx', 'pptx', 'pdf', 'csv', 'video']) {
      expect(keys, `${kind} cannot be filtered for`).toContain(kind)
    }
  })
})

describe('a card draws a thumbnail only when a browser can decode one', () => {
  for (const kind of ['docx', 'xlsx', 'pptx', 'pdf', 'video'] as const) {
    it(`${kind} shows its kind icon, not a broken image`, async () => {
      render(<ArtifactCard art={art(kind)} onOpen={() => {}} />)
      const pane = previewPane()
      expect(pane.querySelector('img'), 'a browser cannot decode these bytes as an image')
        .toBeNull()
      expect(pane.querySelector('svg'), 'no kind icon rendered').toBeTruthy()
      await waitFor(() => expect(fetched).toEqual([]))
    })
  }

  it('an image artifact STILL thumbnails from its raw URL', () => {
    render(<ArtifactCard art={art('image', 'a-chart')} onOpen={() => {}} />)
    const img = previewPane().querySelector('img')
    expect(img, 'the image thumbnail is the regression rail on this change').toBeTruthy()
    expect(img!.getAttribute('src')).toBe('/api/artifacts/a-chart/raw')
  })

  it('a text kind still reads its body as an excerpt', () => {
    render(<ArtifactCard art={art('markdown', 'a-notes')} onOpen={() => {}} />)
    return waitFor(() => expect(fetched).toEqual(['a-notes']))
  })
})

describe('a pdf previews from either source', () => {
  it('the registry routes a kind:pdf artifact to the pdf type', () => {
    expect(resolveContentType({ kind: 'pdf' }).id).toBe('pdf')
    expect(resolveContentType({ name: 'report.pdf' }).id).toBe('pdf')
  })

  it('a GENERATED pdf artifact previews inline from its raw ref', () => {
    const { container } = render(
      <PdfFilePreview content="/api/artifacts/a-pdf/raw" mode="dark" title="Report" />
    )
    const obj = container.querySelector('object')
    expect(obj, 'a generated pdf previewed nothing at all before this').toBeTruthy()
    expect(obj!.getAttribute('data')).toBe('/api/artifacts/a-pdf/raw')
  })

  it('a pdf FILE still previews from its path', () => {
    const { container } = render(
      <PdfFilePreview path="docs/report.pdf" content="" mode="dark" title="Report" />
    )
    const obj = container.querySelector('object')
    expect(obj, 'the pdf FILE half stopped previewing — the half that already worked')
      .toBeTruthy()
    expect(obj!.getAttribute('data')).toBe('/api/file-raw?path=docs%2Freport.pdf&resolve=1')
  })

  it('says so when it has neither, rather than linking to nowhere', () => {
    render(<PdfFilePreview content="" mode="dark" title="Report" />)
    expect(screen.getByText(/no longer available/i)).toBeInTheDocument()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('resolves its source the same way the image renderer already did', () => {
    const pdf = render(<PdfFilePreview content="/api/artifacts/p/raw" mode="dark" title="p" />)
    expect(pdf.container.querySelector('object')!.getAttribute('data'))
      .toBe('/api/artifacts/p/raw')
    const img = render(<ImageFilePreview content="/api/artifacts/i/raw" mode="dark" title="i" />)
    expect(img.container.querySelector('img')!.getAttribute('src')).toBe('/api/artifacts/i/raw')
  })
})
