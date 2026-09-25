import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import VideoPage, { videoInput } from './VideoPage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('video generation workspace', () => {
  it('keeps unavailable generation disabled and discloses actual continuation limits', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<VideoPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Video generation')
    expect(doc.body.textContent).toContain('Loading video capabilities')
    expect(doc.body.textContent).toContain('final frame')
    expect(doc.body.textContent).toContain('does not promise full temporal context')
    expect(doc.body.textContent).toContain('does not verify inference')
    expect(doc.body.textContent).toContain('Originals remain unchanged')
    expect(doc.querySelector('button')?.disabled).toBe(true)
    expect(doc.querySelectorAll('fieldset')).toHaveLength(3)
    for (const field of doc.querySelectorAll('fieldset')) expect(field.disabled).toBe(true)
    expect(doc.querySelector('textarea')?.maxLength).toBe(4000)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=jobs')
  })
  it('serializes exact pinned references and omits unused frame slots', () => {
    const request = videoInput('Animate', 6, '16:9', { seed: 42 }, { first_frame: { id: ' frame ', version: 2 }, last_frame: { id: ' ', version: 1 } })
    expect(request).toEqual({ prompt: 'Animate', duration_seconds: 6, aspect_ratio: '16:9', controls: { seed: 42 }, first_frame_artifact_id: 'frame', first_frame_version: 2 })
    expect(request).not.toHaveProperty('last_frame_artifact_id')
    expect(request).not.toHaveProperty('provider')
    expect(request).not.toHaveProperty('model')
    expect(request).not.toHaveProperty('home')
  })
  it('carries continuation identity without exposing a local file', () => {
    const request = videoInput('Continue', 8, '', {}, { continuation: { id: 'clip', version: 3 } })
    expect(request).toHaveProperty('continuation_artifact_id', 'clip')
    expect(request).toHaveProperty('continuation_version', 3)
    expect(request).not.toHaveProperty('continuation_video')
    expect(request).not.toHaveProperty('continuation_frame')
  })
  it('offers video navigation in an empty sketch workspace', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    expect(doc.querySelector('a[href="#/capabilities/media?view=videos"]')?.textContent).toBe('Generate video')
    expect(doc.querySelector('canvas')).toBeNull()
  })
  it('renders video job provenance and canonical result link', () => {
    const job = { id: 'video', operation: 'video_generate', input: { prompt: 'Moving scene' }, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'succeeded', error: null, result: { artifact_id: 'video-output', version: 1 }, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Video generation')
    expect(doc.querySelector('a')?.textContent).toBe('Open media artifact')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/api/artifacts/video-output/raw?version=1')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('succeeded · attempt 1')
    expect(doc.querySelector('button')).toBeNull()
  })
})
