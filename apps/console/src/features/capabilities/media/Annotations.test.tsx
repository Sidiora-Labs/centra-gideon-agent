import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import Annotations, { AnnotationList } from './Annotations'

describe('media annotation surfaces', () => {
  it('shows the pinned artifact version and honest loading state', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Annotations artifactId="example" version={3} />), 'text/html')
    expect(doc.querySelector('section')?.getAttribute('aria-label')).toBe('Media annotations')
    expect(doc.querySelector('h3')?.textContent).toBe('Notes and attribution · artifact version 3')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('Loading annotations…')
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.querySelector('img')).toBeNull()
    expect(doc.querySelector('video')).toBeNull()
    expect(doc.querySelector('input')).toBeNull()
    expect(doc.querySelector('button')).toBeNull()
    expect(doc.body.textContent).not.toContain('No notes')
  })

  it('renders image notes and normalized bounds without interpreting note HTML', () => {
    const entries = [{ id: 'region', text: '<script>private()</script>', region: [0.1, 0.2, 0.3, 0.4] }]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnnotationList entries={entries} remove={() => {}} />), 'text/html')
    expect(doc.querySelector('script')).toBeNull()
    expect(doc.querySelector('li p')?.textContent).toBe('<script>private()</script>')
    expect(doc.body.textContent).toContain('Image region: 0.1, 0.2, 0.3, 0.4')
    expect(doc.querySelectorAll('li')).toHaveLength(1)
    expect(doc.querySelector('button')?.textContent).toBe('Remove note')
    expect(doc.body.textContent).not.toContain('duration not verified')
    expect(doc.querySelector('a')).toBeNull()
  })

  it('preserves the zero-second video marker and discloses unknown duration', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnnotationList entries={[{ id: 'start', text: 'Opening frame', time_seconds: 0 }]} remove={() => {}} />), 'text/html')
    expect(doc.body.textContent).toContain('Opening frame')
    expect(doc.body.textContent).toContain('At 0 seconds · duration not verified')
    expect(doc.body.textContent).not.toContain('Image region')
    expect(doc.querySelectorAll('button')).toHaveLength(1)
  })

  it('renders independent note rows and preserves authored multiline text', () => {
    const entries = [{ id: 'first', text: 'Line one\nLine two' }, { id: 'second', text: 'Different observation' }]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnnotationList entries={entries} remove={() => {}} />), 'text/html')
    const rows = [...doc.querySelectorAll('li')]
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('p')?.textContent).toBe('Line one\nLine two')
    expect(rows[1].querySelector('p')?.textContent).toBe('Different observation')
    expect(rows.every(row => row.querySelectorAll('button').length === 1)).toBe(true)
    expect(doc.querySelectorAll('input')).toHaveLength(0)
    expect(doc.body.textContent).not.toContain('undefined')
  })

  it('represents an empty list without fabricated note rows', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnnotationList entries={[]} remove={() => {}} />), 'text/html')
    expect(doc.querySelector('ul')).not.toBeNull()
    expect(doc.querySelectorAll('li')).toHaveLength(0)
    expect(doc.querySelectorAll('button')).toHaveLength(0)
    expect(doc.body.textContent).toBe('')
  })
})
