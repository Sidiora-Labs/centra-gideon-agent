import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import SpritePage, { AtlasPreview, editFrame, frameStyle, generationFrames, newFrame } from './SpritePage'
import Page from './Page'
import { JobCard } from './JobsPage'

describe('sprite approval and atlas preview', () => {
  it('requires explicit approval and discloses generation boundaries', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<SpritePage />), 'text/html')
    expect(doc.querySelector('h1')?.textContent).toBe('Sprite production')
    expect(doc.body.textContent).toContain('explicitly approve each exact hash')
    expect(doc.body.textContent).toContain('frames never resize implicitly')
    expect(doc.body.textContent).toContain('does not prove directional or temporal consistency')
    const compile = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Compile approved atlas')
    expect(compile?.disabled).toBe(true)
    const generate = [...doc.querySelectorAll('button')].find(button => button.textContent === 'Generate unapproved frames')
    expect(generate?.disabled).toBe(true)
    expect(doc.querySelectorAll('article')).toHaveLength(0)
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.body.textContent).toContain('Discard frame draft')
  })
  it('invalidates a pixel approval when either pinned reference changes', () => {
    const source = { ...newFrame(1), artifact_id: 'image', version: 3, sha256: 'a'.repeat(64), approved: true }
    expect(editFrame(source, { artifact_id: 'other' })).toEqual({ ...source, artifact_id: 'other', sha256: '', approved: false })
    expect(editFrame(source, { version: 4 })).toEqual({ ...source, version: 4, sha256: '', approved: false })
    expect(editFrame(source, { direction: 'left' }).approved).toBe(true)
    expect(editFrame(source, { name: 'renamed' }).sha256).toBe(source.sha256)
    expect(source.approved).toBe(true)
    expect(source.version).toBe(3)
  })
  it('creates deterministic named directional prompts without invented frames', () => {
    const values = generationFrames('  Walk one\n\nWalk two  ', 'walk', 'left')
    expect(values).toEqual([{ name: 'frame-1', prompt: 'Walk one', animation: 'walk', direction: 'left' }, { name: 'frame-2', prompt: 'Walk two', animation: 'walk', direction: 'left' }])
    expect(values[0]).not.toHaveProperty('artifact_id')
    expect(values[0]).not.toHaveProperty('approved')
    expect(generationFrames('  \n', 'idle', 'down')).toEqual([])
    expect(newFrame(2)).toEqual({ name: 'frame-2', artifact_id: '', version: 1, sha256: '', approved: false, animation: 'idle', direction: 'down' })
  })
  it('uses exact packed rectangles and untrimmed alignment offsets', () => {
    const frame = { ...newFrame(1), frame: { x: 7, y: 1, w: 3, h: 4 }, sourceSize: { w: 8, h: 8 }, spriteSourceSize: { x: 1, y: 2 } }
    const style = frameStyle(frame, { artifact_id: 'atlas', version: 2 })
    expect(style.width).toBe(3)
    expect(style.height).toBe(4)
    expect(style.left).toBe(1)
    expect(style.top).toBe(2)
    expect(style.backgroundPosition).toBe('-7px -1px')
    expect(style.backgroundImage).toBe('url("/api/artifacts/atlas/raw?version=2")')
    expect(style.imageRendering).toBe('pixelated')
    expect(style.position).toBe('absolute')
  })
  it('renders animation and direction selectors with source-sized preview', () => {
    const frame = { ...newFrame(1), frame: { x: 1, y: 1, w: 3, h: 4 }, sourceSize: { w: 8, h: 8 }, spriteSourceSize: { x: 1, y: 2 } }
    const manifest = { fps: 8, atlas: { artifact_id: 'atlas', version: 1 }, frames: [frame, { ...frame, name: 'second', direction: 'up' }] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<AtlasPreview manifest={manifest} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Atlas animation preview')
    expect([...doc.querySelectorAll('option')].map(option => option.textContent)).toEqual(['All frames', 'idle/down', 'idle/up'])
    expect(doc.querySelector('button')?.textContent).toBe('Pause')
    expect(doc.querySelector('[aria-label="Sprite frame"]')?.getAttribute('style')).toContain('width:8px')
    expect(doc.body.textContent).toContain('frame-1')
  })
  it('links generation and compilation jobs back to the same sprite workspace', () => {
    const page = new DOMParser().parseFromString(renderToStaticMarkup(<Page />), 'text/html')
    expect(page.querySelector('nav a[href="#/capabilities/media?view=sprites"]')?.textContent).toBe('Sprite production')
    const job = { id: 'sprite', operation: 'sprite_compile', progress: .5, sketch_id: '', revision: 0, state_revision: 3, attempt: 1, status: 'running', error: null, result: null, events: [] }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<JobCard job={job} busy={false} act={() => {}} />), 'text/html')
    expect(doc.querySelector('h2')?.textContent).toBe('Sprite atlas compilation')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media?view=sprites&job=sprite')
    expect(doc.querySelector('progress')?.getAttribute('value')).toBe('0.5')
  })
})
