import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import TimelinePage, { emptyTimeline, timelineEntry, moveEntry, TimelineTrack } from './TimelinePage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('video timeline editor', () => {
  it('discloses explicit soundtrack and original audio policy', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<TimelinePage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Video timeline')
    expect(doc.body.textContent).toContain('Original clip audio is muted')
    expect(doc.body.textContent).toContain('add an explicit soundtrack')
    expect(doc.body.textContent).toContain('Segment start trims the source')
    expect(doc.body.textContent).toContain('overlay/audio start is timeline placement')
    expect(doc.body.textContent).toContain('300 seconds')
    expect(doc.body.textContent).toContain('Loading timelines')
    expect(doc.querySelector('fieldset')?.disabled).toBe(true)
    expect(doc.querySelectorAll('li')).toHaveLength(0)
    expect(doc.querySelectorAll('section')).toHaveLength(3)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    const save = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Save timeline')
    expect(save?.disabled).toBe(true)
    const render = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Render saved revision')
    expect(render?.disabled).toBe(true)
  })
  it('defines real typed track defaults and independent new drafts', () => {
    expect(emptyTimeline()).toEqual({ title: '', width: 1280, height: 720, fps: 24, segments: [], overlays: [], audio: [] })
    const draft = emptyTimeline()
    draft.segments.push(timelineEntry('segments'))
    expect(emptyTimeline().segments).toEqual([])
    expect(timelineEntry('segments')).toEqual({ artifact_id: '', version: 1, start: 0, duration: 1, kind: 'image' })
    expect(timelineEntry('overlays')).toEqual({ artifact_id: '', version: 1, start: 0, duration: 1, x: 0, y: 0, width: 64, height: 64 })
    expect(timelineEntry('audio')).toEqual({ artifact_id: '', version: 1, start: 0, duration: 1, trim: 0, volume: 1, fade_in: 0, fade_out: 0 })
  })
  it('reorders without mutating the saved revision snapshot', () => {
    const first = { ...timelineEntry('segments'), artifact_id: 'first' }
    const second = { ...timelineEntry('segments'), artifact_id: 'second' }
    const original = [first, second]
    const moved = moveEntry(original, 0, 1)
    expect(moved).toEqual([second, first])
    expect(original).toEqual([first, second])
    expect(moveEntry(original, 0, -1)).toEqual(original)
    expect(moveEntry(original, 1, 1)).toEqual(original)
    expect(moveEntry(moved, 1, -1)).toEqual(original)
    expect(moved[0]).toBe(second)
  })
  it('renders pinning, trim, reorder and removal controls', () => {
    const entries = [{ ...timelineEntry('segments'), artifact_id: 'clip' }, timelineEntry('segments')]
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<TimelineTrack track="segments" entries={entries} change={() => {}} />), 'text/html')
    const rows = [...doc.querySelectorAll('li')]
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('strong')?.textContent).toBe('1')
    expect(rows[1].querySelector('strong')?.textContent).toBe('2')
    expect(rows[0].querySelector('input[type="text"]')?.getAttribute('value')).toBe('clip')
    expect(rows[0].querySelectorAll('input[type="number"]')).toHaveLength(3)
    expect(rows[0].querySelectorAll('option')).toHaveLength(2)
    const firstButtons = [...rows[0].querySelectorAll('button')]
    const secondButtons = [...rows[1].querySelectorAll('button')]
    expect(firstButtons[0].disabled).toBe(true)
    expect(firstButtons[1].disabled).toBe(false)
    expect(secondButtons[0].disabled).toBe(false)
    expect(secondButtons[1].disabled).toBe(true)
    expect(firstButtons[2].textContent).toBe('Remove')
  })
  it('exposes actual overlay placement and audio fades', () => {
    const overlay = new DOMParser().parseFromString(renderToStaticMarkup(<TimelineTrack track="overlays" entries={[timelineEntry('overlays')]} change={() => {}} />), 'text/html')
    expect(overlay.querySelectorAll('input[type="number"]')).toHaveLength(7)
    expect(overlay.body.textContent).toContain('width')
    expect(overlay.body.textContent).toContain('height')
    const audio = new DOMParser().parseFromString(renderToStaticMarkup(<TimelineTrack track="audio" entries={[timelineEntry('audio')]} change={() => {}} />), 'text/html')
    expect(audio.querySelectorAll('input[type="number"]')).toHaveLength(7)
    expect(audio.body.textContent).toContain('fade in')
    expect(audio.body.textContent).toContain('fade out')
    expect(audio.body.textContent).toContain('volume')
    expect(audio.body.textContent).toContain('trim')
  })
  it('wraps every workspace link in accessible wrapping navigation', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    const nav = doc.querySelector('nav[aria-label="Media workspaces"]')
    expect(nav?.classList.contains('flex-wrap')).toBe(true)
    expect(nav?.classList.contains('gap-3')).toBe(true)
    expect(nav?.querySelectorAll('a')).toHaveLength(9)
    expect(nav?.querySelector('a[href="#/capabilities/media?view=timelines"]')?.textContent).toBe('Video timeline')
    expect(doc.querySelector('canvas')).toBeNull()
  })
  it('renders persisted progress and canonical completed output', () => {
    const job = { id: 'timeline', operation: 'timeline_render', progress: .5, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'running', error: null, result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Timeline render')
    expect(doc.querySelector('progress')?.getAttribute('value')).toBe('0.5')
    expect(doc.querySelector('progress')?.getAttribute('max')).toBe('1')
    expect(doc.querySelector('button')?.textContent).toBe('Cancel')
  })
})
