import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { JobCard, mediaArtifactRawUrl, type MediaJob } from './JobsPage'
import { ImageGeneration } from '../../../shared/vendor/assistant-ui/elements/image-generation'

afterEach(cleanup)

const prompt = 'A lunar harbor'
const job: MediaJob = {
  id: 'image-job', operation: 'image_generate', input: { prompt },
  sketch_id: '', revision: 0, state_revision: 3, attempt: 1,
  status: 'queued', error: null, result: null,
  events: [{ status: 'queued', at: '2026-09-26T00:00:00Z', detail: 'Await worker' }],
}

function documentFor(value: MediaJob) {
  return new DOMParser().parseFromString(
    renderToStaticMarkup(<JobCard job={value} act={() => {}} busy={false} />), 'text/html',
  )
}

describe('real image generation media jobs', () => {
  it('renders a queued donor with the recorded prompt once and keeps Cancel/history', () => {
    const doc = documentFor(job)
    const donor = doc.querySelector('[data-slot="image-generation"]')
    expect(donor).not.toBeNull()
    expect(doc.querySelector('h2')?.textContent).toBe(`Image generation: ${prompt}`)
    expect(doc.body.textContent?.split(prompt)).toHaveLength(2)
    expect(donor?.textContent).toContain('Queued')
    expect(donor?.querySelector('img')).toBeNull()
    expect(doc.querySelector('button')?.textContent).toBe('Cancel')
    expect(doc.querySelector('summary')?.textContent).toBe('Attempt history')
    expect(doc.querySelector('details')?.textContent).toContain('Await worker')
    expect(doc.querySelector('[aria-label="Regenerate image"]')).toBeNull()
  })

  it('labels running and cancellation-pending states without exposing an output', () => {
    for (const [status, label] of [['running', 'Generating'], ['cancel_requested', 'Cancel requested']]) {
      const doc = documentFor({ ...job, status })
      const donor = doc.querySelector('[data-slot="image-generation"]')
      expect(donor?.textContent).toContain(label)
      expect(doc.body.textContent?.split(prompt)).toHaveLength(2)
      expect(donor?.querySelector('img')).toBeNull()
      expect(doc.querySelector('a[href^="/api/artifacts/"]')).toBeNull()
      expect(doc.querySelector('[aria-label="Regenerate image"]')).toBeNull()
    }
  })

  it('uses the exact encoded result version in preview, open link, and attempt history', () => {
    const result = { artifact_id: 'folder/moon harbor?', version: 3 }
    const previous = { artifact_id: 'prior/image', version: 2 }
    const doc = documentFor({ ...job, status: 'succeeded', result, events: [
      { status: 'succeeded', at: '2026-09-26T01:00:00Z', detail: 'Saved', result: previous },
    ] })
    const preview = doc.querySelector<HTMLImageElement>('[data-slot="image-generation"] img')
    const url = '/api/artifacts/folder%2Fmoon%20harbor%3F/raw?version=3'
    expect(preview?.getAttribute('src')).toBe(url)
    expect(preview?.getAttribute('alt')).toBe(prompt)
    expect(doc.querySelector('a[href^="/api/artifacts/"]')?.getAttribute('href')).toBe(url)
    expect(doc.querySelector('details a')?.getAttribute('href')).toBe('/api/artifacts/prior%2Fimage/raw?version=2')
    expect(doc.body.textContent?.split(prompt)).toHaveLength(2)
    expect(doc.querySelectorAll('button')).toHaveLength(0)
    expect(doc.querySelector('[aria-label="Regenerate image"]')).toBeNull()
  })

  it('retains the native Retry action on failure without manufacturing an image', () => {
    const act = vi.fn()
    render(<JobCard job={{ ...job, status: 'failed', error: 'Provider unavailable' }} act={act} busy={false} />)
    expect(screen.getByRole('alert').textContent).toBe('Provider unavailable')
    expect(screen.getByText('Image unavailable')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Regenerate image' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(act).toHaveBeenCalledWith(expect.objectContaining({ id: 'image-job', status: 'failed' }), 'retry')
  })

  it('does not mount an image donor for other media operations', () => {
    const doc = documentFor({ ...job, operation: 'video_generate', status: 'succeeded',
      result: { artifact_id: 'video-output', version: 2 } })
    expect(doc.querySelector('[data-slot="image-generation"]')).toBeNull()
    expect(doc.querySelector('a[href^="/api/artifacts/"]')?.getAttribute('href'))
      .toBe('/api/artifacts/video-output/raw?version=2')
    expect(doc.querySelector('summary')?.textContent).toBe('Attempt history')
    const generic = documentFor({ ...job, operation: undefined, status: 'succeeded',
      result: { artifact_id: 'sketch-output', version: 1 } })
    expect(generic.querySelector('[data-slot="image-generation"]')).toBeNull()
    expect(generic.querySelector('a[href^="/api/artifacts/"]')).not.toBeNull()
  })

  it('retains a supplied donor caption and action independently of JobCard retry', () => {
    const regenerate = vi.fn()
    const { container, rerender } = render(<ImageGeneration prompt={prompt} generating statusLabel="Queued" />)
    expect(container.querySelector('[data-slot="image-generation"] p')?.textContent).toBe(`Queued · ${prompt}`)
    rerender(<ImageGeneration prompt={prompt} generating={false} showPrompt={false} onRegenerate={regenerate} />)
    expect(screen.queryByText(prompt)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate image' }))
    expect(regenerate).toHaveBeenCalledOnce()
  })
})

describe('canonical media artifact URLs', () => {
  it('encodes IDs and versions without treating artifact identity as a URL', () => {
    expect(mediaArtifactRawUrl({ artifact_id: 'folder/a ?#', version: 4 }))
      .toBe('/api/artifacts/folder%2Fa%20%3F%23/raw?version=4')
    expect(mediaArtifactRawUrl({ artifact_id: 'https://example.test/picture', version: 1 }))
      .toBe('/api/artifacts/https%3A%2F%2Fexample.test%2Fpicture/raw?version=1')
  })

  it('omits an unrecorded or invalid version and refuses an absent artifact ID', () => {
    expect(mediaArtifactRawUrl({ artifact_id: 'current' })).toBe('/api/artifacts/current/raw')
    expect(mediaArtifactRawUrl({ artifact_id: 'current', version: 0 })).toBe('/api/artifacts/current/raw')
    expect(mediaArtifactRawUrl({ artifact_id: 'current', version: 1.5 })).toBe('/api/artifacts/current/raw')
    expect(mediaArtifactRawUrl({ artifact_id: '' })).toBeNull()
    expect(mediaArtifactRawUrl(null)).toBeNull()
    expect(mediaArtifactRawUrl(undefined)).toBeNull()
  })
})
