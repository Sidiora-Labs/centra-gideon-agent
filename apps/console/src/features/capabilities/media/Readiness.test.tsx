import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import Readiness, { ReadinessCards, type ReadinessRow } from './Readiness'

const row: ReadinessRow = { capability: 'image_gen', selection: '', status: 'unconfigured', available: false, inference_verified: false, management_available: false, reason: 'Select a model in Settings', model: null }
const docFor = (items: ReadinessRow[]) => new DOMParser().parseFromString(renderToStaticMarkup(<ReadinessCards items={items} />), 'text/html')
describe('media readiness observations', () => {
  it('discloses observation limitations and loads cached data initially', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Readiness />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Media readiness')
    expect(doc.body.textContent).toContain('do not prove credentials')
    expect(doc.body.textContent).toContain('GPU capacity')
    expect(doc.body.textContent).toContain('does not generate media or install models')
    expect(doc.body.textContent).toContain('Loading last observation')
    expect(doc.querySelector('button')?.textContent).toBe('Refresh readiness')
    expect(doc.querySelectorAll('article')).toHaveLength(0)
    expect(doc.querySelectorAll('a')).toHaveLength(3)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })
  it('shows unconfigured image state without claiming model availability', () => {
    const doc = docFor([row])
    expect(doc.querySelector('h2')?.textContent).toBe('Image generation')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('unconfigured')
    expect(doc.body.textContent).toContain('No model selected')
    expect(doc.body.textContent).toContain('Inference has not been verified')
    expect(doc.querySelector('dl')).toBeNull()
    expect(doc.body.textContent).toContain('not registered')
    expect(doc.querySelector('button')).toBeNull()
  })
  it('shows configured image capabilities as catalog metadata only', () => {
    const doc = docFor([{ ...row, selection: 'provider:image', status: 'configured', available: true, management_available: true, model: { name: 'image', downloaded: true, sizes: ['512x512'], supports_edit: true } }])
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('configured')
    expect(doc.body.textContent).toContain('provider:image')
    expect(doc.body.textContent).toContain('512x512')
    expect(doc.body.textContent).toContain('Supported')
    expect(doc.body.textContent).toContain('Catalog reports present')
    expect(doc.body.textContent).toContain('Inference has not been verified')
    expect(doc.body.textContent).toContain('available in provider settings')
    expect(doc.body.textContent).not.toContain('Maximum duration')
  })
  it('shows actual video metadata without image controls', () => {
    const doc = docFor([{ ...row, capability: 'video_gen', selection: 'video:clip', model: { name: 'clip', downloaded: true, aspect_ratios: ['16:9'], max_duration_s: 8 } }])
    expect(doc.querySelector('h2')?.textContent).toBe('Video generation')
    expect(doc.body.textContent).toContain('16:9')
    expect(doc.body.textContent).toContain('8 seconds')
    expect(doc.body.textContent).not.toContain('Image editing')
    expect(doc.body.textContent).not.toContain('Sizes')
    expect(doc.querySelectorAll('article')).toHaveLength(1)
  })
  it('escapes configuration labels and does not hide unavailable reasons', () => {
    const doc = docFor([{ ...row, selection: '<script>secret()</script>', status: 'unavailable', reason: '<b>Check configuration</b>' }])
    expect(doc.querySelector('script')).toBeNull()
    expect(doc.querySelector('b')).toBeNull()
    expect(doc.body.textContent).toContain('<script>secret()</script>')
    expect(doc.body.textContent).toContain('<b>Check configuration</b>')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('unavailable')
    expect(doc.querySelector('dl')).toBeNull()
  })
})
