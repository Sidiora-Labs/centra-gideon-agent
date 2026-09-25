import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import EpisodePage, { changeSceneMode, newEpisode, SceneRows } from './EpisodePage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('continuous episode planning', () => {
  it('discloses provider, continuation and assembly boundaries', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<EpisodePage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Continuous episodes')
    expect(doc.body.textContent).toContain('credentials are required')
    expect(doc.body.textContent).toContain('Completed clips survive retries')
    expect(doc.body.textContent).toContain('only the final frame')
    expect(doc.body.textContent).toContain('explicit consent per scene')
    expect(doc.body.textContent).toContain('Assembly mutes clip audio')
    expect(doc.body.textContent).toContain('trimmed clip must be saved as a separate artifact')
    expect(doc.querySelector('fieldset')?.disabled).toBe(true)
    expect(doc.body.textContent).toContain('Loading episodes')
    expect(doc.querySelectorAll('li')).toHaveLength(0)
    const save = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Validate and save')
    expect(save?.disabled).toBe(true)
    const render = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Render saved episode')
    expect(render?.disabled).toBe(true)
  })
  it('removes stale input pins when switching reuse to generation', () => {
    const reused = { prompt: 'Scene', duration_seconds: 4, mode: 'reuse' as const, allow_fallback: false, artifact_id: 'old', version: 3 }
    const establish = changeSceneMode(reused, 'establish')
    expect(establish).toEqual({ prompt: 'Scene', duration_seconds: 4, mode: 'establish', allow_fallback: false })
    expect(establish).not.toHaveProperty('artifact_id')
    expect(establish).not.toHaveProperty('version')
    expect(reused.artifact_id).toBe('old')
    const continuation = changeSceneMode(reused, 'continue')
    expect(continuation).not.toHaveProperty('artifact_id')
    expect(continuation.mode).toBe('continue')
    expect(changeSceneMode(establish, 'reuse')).toEqual({ ...reused, artifact_id: '', version: 1 })
  })
  it('creates independent plans with no invented output clip', () => {
    const first = newEpisode(), second = newEpisode()
    expect(first).toEqual({ title: '', width: 1280, height: 720, fps: 24, aspect_ratio: '16:9', scenes: [] })
    first.title = 'Changed'
    expect(second.title).toBe('')
    expect(first.scenes).not.toBe(second.scenes)
    expect(first).not.toHaveProperty('result')
  })
  it('renders real lineage, fallback disclosure and canonical clip references', () => {
    const scenes = [{ position: 0, status: 'succeeded', result: { artifact_id: 'first', version: 2 } }, { position: 1, status: 'failed', predecessor: { artifact_id: 'first', version: 2 }, fallback: true, error: 'Provider unavailable' }]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<SceneRows scenes={scenes} />), 'text/html')
    expect(doc.querySelectorAll('li')).toHaveLength(2)
    expect(doc.querySelector('li')?.textContent).toContain('Scene 1: succeeded')
    expect(doc.body.textContent).toContain('predecessor first v2')
    expect(doc.querySelector('strong')?.textContent).toContain('fresh establishment fallback')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('/api/artifacts/first/raw?version=2')
    expect(doc.querySelector('[role="alert"]')?.textContent).toBe('Provider unavailable')
  })
  it('offers episodes before any sketch exists and shows scene progress on jobs', () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    expect(page.querySelector('nav a[href="#/capabilities/media?view=episodes"]')?.textContent).toBe('Continuous episodes')
    expect(page.querySelector('canvas')).toBeNull()
    const job = { id: 'episode', operation: 'episode_render', progress: .5, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'running', error: null, result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Episode render')
    expect(doc.querySelector('h3')?.textContent).toBe('Scene progress and lineage')
    expect(doc.querySelector('progress')?.getAttribute('value')).toBe('0.5')
    expect(doc.querySelector('button')?.textContent).toBe('Cancel')
  })
})
