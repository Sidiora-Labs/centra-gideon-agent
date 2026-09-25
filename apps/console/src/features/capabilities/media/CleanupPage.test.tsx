import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import CleanupPage, { TransformList, defaultTransform } from './CleanupPage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('local image cleanup workspace', () => {
  it('discloses deterministic cleanup and keeps empty request disabled', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<CleanupPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Image cleanup')
    expect(doc.body.textContent).toContain('EXIF-oriented copy')
    expect(doc.body.textContent).toContain('original stays unchanged')
    expect(doc.body.textContent).toContain('Lanczos interpolation')
    expect(doc.body.textContent).toContain('not semantic segmentation')
    expect(doc.body.textContent).toContain('counterclockwise right angles')
    expect(doc.body.textContent).toContain('4 megapixels')
    expect(doc.querySelectorAll('option')).toHaveLength(8)
    expect(doc.querySelectorAll('li')).toHaveLength(0)
    const queue = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Queue cleanup')
    expect(queue?.disabled).toBe(true)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })
  it('offers cleanup navigation before any sketch exists', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    expect(doc.querySelector('nav button[aria-label="Image cleanup"]')?.textContent).toBe('Image cleanup')
    expect(doc.querySelector('nav button[aria-label="Generate image"]')?.textContent).toBe('Generate image')
    expect(doc.querySelector('canvas')).toBeNull()
  })
  it('provides exact operation parameters and ordered editing affordances', () => {
    const operations = [defaultTransform('crop'), defaultTransform('resize'), defaultTransform('solid_background')]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<TransformList operations={operations} change={() => {}} />), 'text/html')
    const rows = [...doc.querySelectorAll('li')]
    expect(rows).toHaveLength(3)
    expect(rows[0].querySelector('h2')?.textContent).toBe('1. crop')
    expect(rows[1].querySelector('h2')?.textContent).toBe('2. resize')
    expect(rows[2].querySelector('h2')?.textContent).toBe('3. solid_background')
    expect(rows[0].querySelector('button')?.disabled).toBe(true)
    expect(rows[1].querySelector('button')?.disabled).toBe(false)
    expect(rows[0].querySelectorAll('input')).toHaveLength(4)
    expect(rows[1].querySelectorAll('input')).toHaveLength(2)
    expect(rows[2].querySelector('input[type="color"]')?.getAttribute('value')).toBe('#ffffff')
    expect(rows[2].querySelector('input[type="number"]')?.getAttribute('value')).toBe('10')
  })
  it('starts rotation, flip, sharpness and color adjustments with explicit defaults', () => {
    expect(defaultTransform('rotate')).toEqual({ op: 'rotate', degrees: 90 })
    expect(defaultTransform('flip')).toEqual({ op: 'flip', axis: 'horizontal' })
    expect(defaultTransform('brightness')).toEqual({ op: 'brightness', factor: 1 })
    expect(defaultTransform('contrast')).toEqual({ op: 'contrast', factor: 1 })
    expect(defaultTransform('sharpen')).toEqual({ op: 'sharpen', radius: 2, percent: 150, threshold: 3 })
    expect(defaultTransform('resize')).toEqual({ op: 'resize', width: 512, height: 512 })
  })
  it('labels cleanup jobs without pretending model generation', () => {
    const job = { id: 'cleanup', operation: 'image_cleanup', sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'succeeded', error: null, result: { artifact_id: 'output', version: 1 }, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Image cleanup')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/api/artifacts/output/raw?version=1')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('succeeded · attempt 1')
    expect(doc.querySelector('button')).toBeNull()
  })
})
