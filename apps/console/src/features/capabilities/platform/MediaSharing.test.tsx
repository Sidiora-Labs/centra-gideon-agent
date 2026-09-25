import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import MediaSharing from './MediaSharing'

describe('selective media sharing', () => {
  it('explains explicit selection and narrow category policy', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<MediaSharing />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Selective media sharing')
    expect(doc.body.textContent).toContain('one explicit immutable image or video version')
    expect(doc.body.textContent).toContain('media.assets')
    expect(doc.body.textContent).toContain('no library index, folders, annotations, or other datastore records')
    expect(doc.body.textContent).toContain('Loading media shares')
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })

  it('does not fabricate a peer, artifact, receipt or successful transfer', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<MediaSharing />), 'text/html')
    expect(doc.querySelector('input')).toBeNull()
    expect(doc.body.textContent).not.toContain('Share selected version')
    expect(doc.body.textContent).not.toContain('Sent shares')
    expect(doc.body.textContent).not.toContain('active')
    expect(doc.body.textContent).not.toContain('succeeded')
  })

  it('accepts an isolated platform API base without leaking it into static markup', () => {
    const html = renderToStaticMarkup(<MediaSharing baseUrl="/tenant/example" />)
    expect(html).toContain('Selective media sharing')
    expect(html).not.toContain('/api/capabilities/platform/media-shares')
  })
})
