import { describe, it, expect, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render } from '@testing-library/react'
import { PROSE_MEASURE, PROSE_MEASURE_CLASS } from '../../theme/measure'
import { DocumentPreview } from './renderers'
import { exportDocumentHtml } from './exporters'


const SRC = join(process.cwd(), "src")
const RENDERERS = join(SRC, 'shared/ui/content/renderers.tsx')
const EXPORTERS = join(SRC, 'shared/ui/content/exporters.ts')

function captureDownload(): { blobs: Blob[]; restore: () => void } {
  const blobs: Blob[] = []
  const create = vi.fn((b: Blob) => { blobs.push(b); return 'blob:captured' })
  const prevCreate = URL.createObjectURL
  const prevRevoke = URL.revokeObjectURL
  URL.createObjectURL = create as unknown as typeof URL.createObjectURL
  URL.revokeObjectURL = (() => {}) as unknown as typeof URL.revokeObjectURL
  return {
    blobs,
    restore: () => { URL.createObjectURL = prevCreate; URL.revokeObjectURL = prevRevoke },
  }
}

describe('the prose measure is one token', () => {
  it('is stated in rem, at the measured value', () => {
    expect(PROSE_MEASURE).toBe('35rem')
    expect(PROSE_MEASURE).toMatch(/^\d+(\.\d+)?rem$/)
    const rem = Number.parseFloat(PROSE_MEASURE)
    expect(rem).toBeGreaterThanOrEqual(21)
    expect(rem).toBeLessThanOrEqual(42)
  })

  it('offers a Tailwind form that cannot drift from the length', () => {
    expect(PROSE_MEASURE_CLASS).toBe(`max-w-[${PROSE_MEASURE}]`)
  })
})

describe('the document preview applies the token', () => {
  it('renders the measure class on the .doc container, not 72ch', () => {
    const { container } = render(
      <DocumentPreview content="<p>Editorial body</p>" mode="dark" title="A document" />,
    )
    const doc = container.querySelector('.doc')
    expect(doc, 'the document branch must have rendered a .doc container').not.toBeNull()
    expect(doc!.innerHTML).toContain('Editorial body')

    expect(doc!.className.split(/\s+/)).toContain(PROSE_MEASURE_CLASS)
    expect(doc!.className).not.toContain('72ch')
  })
})

describe('the HTML export carries the same measure', () => {
  it('emits the converged measure in its stylesheet and no 72ch', async () => {
    const cap = captureDownload()
    try {
      exportDocumentHtml('<p>Exported body</p>', 'Doc')
      expect(cap.blobs, 'exportDocumentHtml must have produced one downloadable blob').toHaveLength(1)
      const html = await cap.blobs[0].text()

      expect(html).toContain('<main>')
      expect(html).toContain('Exported body')

      expect(html).toContain(`max-width: ${PROSE_MEASURE}`)
      expect(html).not.toContain('72ch')
      expect(html).not.toContain('var(--')
    } finally {
      cap.restore()
    }
  })
})

describe('72ch is retired from both consumers', () => {
  const files: Array<[string, string, string]> = [
    ['shared/ui/content/renderers.tsx', RENDERERS, 'DocumentPreview'],
    ['shared/ui/content/exporters.ts', EXPORTERS, 'exportDocumentHtml'],
  ]

  it.each(files)('%s is found, non-trivial, and reads the shared token', (rel, abs, anchor) => {
    const src = readFileSync(abs, 'utf8')
    expect(src.split('\n').length, `${rel} looks truncated`).toBeGreaterThan(50)
    expect(src, `${rel} is not the file this rail was written against`).toContain(anchor)
    expect(src, `${rel} must import the shared prose measure`).toMatch(
      /from '\.\.\/\.\.\/theme\/measure'/,
    )
  })

  it.each(files)('%s contains no 72ch', (rel, abs) => {
    const src = readFileSync(abs, 'utf8')
    expect(src.includes('72ch'), `${rel} still caps prose at 72ch`).toBe(false)
  })
})
