import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { Artifact } from '../../shared/data/api'
import { ArtifactCard } from './ArtifactCard'
import { PdfFilePreview, ImageFilePreview } from '../../shared/ui/content/renderers'
import { registerBuiltinContentTypes } from '../../shared/ui/content/registerBuiltins'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import { ARTIFACT_KINDS, artifactKindMeta } from '../files/fileMeta'


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

const art = (kind: string, slug = `a-${kind}`): Artifact => ({
  slug, name: `Quarterly report.${kind}`, kind, source: 'chat', version: 1,
  created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
} as unknown as Artifact)

function previewPane(): HTMLElement {
  const tile = screen.getByRole('button')
  return tile.firstElementChild as HTMLElement
}

beforeEach(() => { fetched.length = 0 })

describe('a generated document is labelled by its real kind', () => {
  it('names the format instead of falling through to "Widget"', () => {
    const km = artifactKindMeta('docx')
    expect(km.label, 'a Word document read "Widget" from v0.1.0 to 0.1.3').not.toBe('Widget')
    expect(km.label).toMatch(/word/i)
  })

  it('renders that label on the card, not the fallback kind', () => {
    render(<ArtifactCard art={art('docx')} onOpen={() => {}} />)
    expect(screen.getByText(artifactKindMeta('docx').label)).toBeInTheDocument()
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
  for (const kind of ['docx', 'xlsx', 'pptx', 'pdf', 'video']) {
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
