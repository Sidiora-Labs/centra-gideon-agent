import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import NativeDuplex from './NativeDuplex'

describe('native duplex audio', () => {
  it('states the existing-call and external approval boundary', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<NativeDuplex />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Native duplex audio')
    expect(doc.body.textContent).toContain('existing native call request')
    expect(doc.body.textContent).toContain('does not dial, answer, or hang up')
    expect(doc.body.textContent).toContain('explicit approval')
    expect(doc.body.textContent).toContain('Loading native audio readiness')
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })

  it('does not claim device readiness or render controls before the real response', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<NativeDuplex />), 'text/html')
    expect(doc.body.textContent).not.toContain('locally available')
    expect(doc.body.textContent).not.toContain('Capture and transcribe turn')
    expect(doc.body.textContent).not.toContain('Synthesize and play reply')
    expect(doc.body.textContent).not.toContain('Attach audio session')
    expect(doc.querySelector('audio')).toBeNull()
  })

  it('uses the isolated experience API base', () => {
    const html = renderToStaticMarkup(<NativeDuplex baseUrl="/tenant/example/api/capabilities/experience" />)
    expect(html).toContain('Native duplex audio')
    expect(html).not.toContain('/api/capabilities/experience/native-duplex')
  })
})
