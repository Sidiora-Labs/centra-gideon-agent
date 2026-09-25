import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import DownloadPage, { artifactUrl } from './DownloadPage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('media source downloader', () => {
  it('renders guarded acquisition without fabricated progress or success', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<DownloadPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Media source downloader')
    expect(doc.body.textContent).toContain('bounded public YouTube source')
    expect(doc.body.textContent).toContain('Every request and redirect is checked')
    expect(doc.body.textContent).toContain('canonical artifact with source provenance')
    expect(doc.querySelector('[role="status"]')).toBeNull()
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.body.textContent).not.toContain('succeeded')
    expect(doc.body.textContent).not.toContain('Download canonical video')
  })

  it('starts with bounded inputs and requires a source URL', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<DownloadPage />), 'text/html')
    const input = doc.querySelector('input[type="url"]')
    expect(input?.getAttribute('maxlength')).toBe('2000')
    expect(input?.getAttribute('placeholder')).toContain('youtube.com/watch')
    expect([...doc.querySelectorAll('option')].map(option => [option.value, option.textContent])).toEqual([['video', 'Video'], ['audio', 'Audio']])
    expect((doc.querySelector('select') as HTMLSelectElement).value).toBe('video')
    const submit = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Queue guarded download')
    expect(submit?.disabled).toBe(true)
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=jobs')
  })

  it('builds only a version-pinned canonical artifact URL', () => {
    expect(artifactUrl(null)).toBe('')
    expect(artifactUrl({ id: 'queued', status: 'queued' })).toBe('')
    expect(artifactUrl({ id: 'done', status: 'succeeded', result: { artifact_id: 'source / clip', version: 4, kind: 'video' } })).toBe('/api/artifacts/source%20%2F%20clip/raw?version=4')
  })

  it('registers a unique downloader destination in media navigation', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    const destinations = doc.querySelectorAll('nav button[aria-label="Source downloader"]')
    expect(destinations).toHaveLength(1)
    expect(destinations[0].textContent).toBe('Source downloader')
  })

  it('links queued download jobs to the exact downloader record', () => {
    const job = { id: 'download-job', operation: 'source_download', input: { url: 'https://youtu.be/abcdefghijk', kind: 'audio' }, sketch_id: '', revision: 0, state_revision: 1, attempt: 0, status: 'queued', error: null, result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Source audio download')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=downloads&job=download-job')
    expect(doc.body.textContent).toContain('queued · attempt 0')
    expect([...doc.querySelectorAll('button')].map(button => button.textContent)).toContain('Cancel')
  })

  it('shows a completed artifact only when the job has a result', () => {
    const job = { id: 'download-job', operation: 'source_download', input: { url: 'https://youtu.be/abcdefghijk', kind: 'video' }, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'succeeded', error: null, result: { artifact_id: 'media-source-download-job', version: 1 }, events: [{ status: 'succeeded', at: 'now', detail: 'finished', result: { artifact_id: 'media-source-download-job', version: 1 } }] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    const urls = [...doc.querySelectorAll('a')].map(link => link.getAttribute('href'))
    expect(urls).toContain('/api/artifacts/media-source-download-job/raw?version=1')
    expect(doc.body.textContent).toContain('succeeded · attempt 1')
    expect([...doc.querySelectorAll('button')]).toHaveLength(0)
  })
})
