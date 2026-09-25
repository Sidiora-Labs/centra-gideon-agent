import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import JobsPage, { JobCard, type MediaJob } from './JobsPage'

function documentFor(job: MediaJob, busy = false) {
  return new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={busy} act={() => {}} />), 'text/html')
}
const job: MediaJob = { id: 'job', sketch_id: 'sketch', revision: 4, state_revision: 2, attempt: 1, status: 'running', error: null, result: null, events: [{ status: 'queued', at: '2026-09-25', detail: 'Await worker' }] }
describe('durable media job rendering', () => {
  it('shows worker waiting and cooperative cancellation boundaries', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobsPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Media jobs')
    expect(doc.body.textContent).toContain('supervised media worker')
    expect(doc.body.textContent).toContain('disabled')
    expect(doc.body.textContent).toContain('completed output is retained')
    expect(doc.querySelectorAll('article')).toHaveLength(0)
    expect(doc.querySelectorAll('a')).toHaveLength(2)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
  })
  it('shows running attempt with cancellation and history', () => {
    const doc = documentFor(job)
    expect(doc.querySelector('h2')?.textContent).toContain('revision 4')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('running · attempt 1')
    expect(doc.querySelector('button')?.textContent).toBe('Cancel')
    expect(doc.querySelector('button')?.disabled).toBe(false)
    expect(doc.querySelector('summary')?.textContent).toBe('Attempt history')
    expect(doc.querySelector('details')?.textContent).toContain('Await worker')
    expect(doc.querySelector('a')).toBeNull()
  })
  it('preserves failed attempt error and disables action while busy', () => {
    const doc = documentFor({ ...job, status: 'failed', error: '<b>source unavailable</b>' }, true)
    expect(doc.querySelector('[role="alert"]')?.textContent).toBe('<b>source unavailable</b>')
    expect(doc.querySelector('b')).toBeNull()
    expect(doc.querySelector('button')?.textContent).toBe('Retry')
    expect(doc.querySelector('button')?.disabled).toBe(true)
    expect(doc.querySelectorAll('button')).toHaveLength(1)
  })
  it('links both completed output and immutable prior attempt output', () => {
    const result = { artifact_id: 'png-output', version: 3 }
    const doc = documentFor({ ...job, status: 'cancelled', result, events: [{ status: 'cancelled', at: 'today', detail: 'Cancelled after render', attempt: 1, result }] })
    expect(doc.querySelectorAll('a')).toHaveLength(2)
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/api/artifacts/png-output/raw?version=3')
    expect(doc.querySelector('details a')?.textContent).toBe('Attempt output')
    expect(doc.querySelector('button')?.textContent).toBe('Retry')
  })
  it('does not offer invalid actions for successful or cancellation-pending jobs', () => {
    for (const status of ['succeeded', 'cancel_requested']) {
      const doc = documentFor({ ...job, status })
      expect(doc.querySelector('button')).toBeNull()
      expect(doc.querySelector('[role="status"]')?.textContent).toContain(status)
      expect(doc.querySelector('details')).not.toBeNull()
    }
  })
})
