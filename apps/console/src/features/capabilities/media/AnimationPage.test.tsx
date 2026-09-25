import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import AnimationPage, { downloadUrl, initialAnimation, previewUrl } from './AnimationPage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('code animation workspace', () => {
  it('explains real model generation and external availability boundary', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnimationPage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Code animation')
    expect(doc.body.textContent).toContain('selected reasoning model')
    expect(doc.body.textContent).toContain('exact validated model response')
    expect(doc.body.textContent).toContain('External network access and assets are rejected')
    expect(doc.body.textContent).toContain('does not claim external model availability')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=jobs')
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.querySelector('iframe')).toBeNull()
  })

  it('starts from bounded format defaults and requires a real brief', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnimationPage />), 'text/html')
    const fields = [...doc.querySelectorAll('input[type="number"]')]
    expect(fields.map(field => field.getAttribute('value'))).toEqual(['20', '1280', '720', '30'])
    expect([...doc.querySelectorAll('option')].map(option => option.textContent)).toEqual(['canvas2d', 'svg', 'css'])
    expect((doc.querySelector('select') as HTMLSelectElement).value).toBe('canvas2d')
    expect(doc.querySelector('input[type="checkbox"]')?.hasAttribute('checked')).toBe(false)
    const generate = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Generate with reasoning model')
    expect(generate?.disabled).toBe(true)
    expect(initialAnimation).toEqual({ title: '', concept: '', renderer: 'canvas2d', duration_seconds: 20, width: 1280, height: 720, fps: 30, interactive: false })
  })

  it('builds an encoded canonical preview URL only after completion', () => {
    const pending = { id: 'job', status: 'queued' }
    const finished = { id: 'job', status: 'succeeded', result: { artifact_id: 'animation / one', version: 3, frame: { width: 640, height: 360 } } }
    expect(previewUrl(pending)).toBe('')
    expect(previewUrl(finished)).toBe('/api/capabilities/media/jobs/job/animation')
    expect(downloadUrl(finished)).toBe('/api/artifacts/animation%20%2F%20one/raw?version=3')
    expect(downloadUrl(pending)).toBe('')
  })

  it('registers the animation workspace in media navigation', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    const link = doc.querySelector('nav a[href="#/capabilities/media?view=animation"]')
    expect(link?.textContent).toBe('Code animation')
    expect([...doc.querySelectorAll('nav a')].filter(item => item.textContent === 'Code animation')).toHaveLength(1)
  })

  it('links animation jobs back to the exact workspace', () => {
    const job = { id: 'animation-job', operation: 'code_animation_generate', input: { title: 'Orbital Bloom' }, sketch_id: '', revision: 0, state_revision: 2, attempt: 1, status: 'running', error: null, result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Code animation: Orbital Bloom')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=animation&job=animation-job')
    expect(doc.body.textContent).toContain('running · attempt 1')
    expect([...doc.querySelectorAll('button')].map(button => button.textContent)).toContain('Cancel')
  })

  it('does not present a fabricated preview or success in the initial state', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AnimationPage />), 'text/html')
    expect(doc.querySelector('[role="status"]')).toBeNull()
    expect(doc.body.textContent).not.toContain('succeeded')
    expect(doc.body.textContent).not.toContain('Download canonical HTML')
    expect(doc.body.textContent).not.toContain('Sandboxed preview')
  })
})
