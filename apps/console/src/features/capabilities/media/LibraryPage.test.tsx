import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import LibraryPage, { MediaCard, type MediaItem } from './LibraryPage'

const item: MediaItem = {
  id: 'example-image', name: 'A blue image', kind: 'image', mime: 'image/png', version: 2,
  updated_at: '2026-09-25T00:00:00+00:00', tags: ['blue', 'study'], collection: 'Sketchbook',
  readonly: false, raw_url: '/api/artifacts/example-image/raw?version=2', provenance: {},
}

describe('media library content surface', () => {
  it('renders image artifact preview against its pinned canonical version', () => {
    const html = renderToStaticMarkup(<MediaCard item={item} onSelect={() => {}} />)
    const doc = new DOMParser().parseFromString(html, 'text/html')
    const image = doc.querySelector('img')
    expect(image?.getAttribute('src')).toBe(item.raw_url)
    expect(image?.getAttribute('alt')).toBe('A blue image')
    expect(image?.getAttribute('loading')).toBe('lazy')
    expect(doc.querySelector('video')).toBeNull()
    expect(doc.querySelector('button')?.textContent).toBe('A blue image')
    expect(doc.body.textContent).toContain('image · v2')
    expect(doc.body.textContent).toContain('Sketchbook · blue, study')
    expect(doc.querySelector('article')?.className).toContain('min-w-0')
  })

  it('renders existing video with native playback controls and metadata loading', () => {
    const video = { ...item, kind: 'video', mime: 'video/mp4', name: 'A short clip', raw_url: '/api/artifacts/clip/raw?version=1' }
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<MediaCard item={video} onSelect={() => {}} />), 'text/html')
    const player = doc.querySelector('video')
    expect(player?.getAttribute('src')).toBe(video.raw_url)
    expect(player?.getAttribute('aria-label')).toBe('A short clip')
    expect(player?.hasAttribute('controls')).toBe(true)
    expect(player?.getAttribute('preload')).toBe('metadata')
    expect(player?.hasAttribute('autoplay')).toBe(false)
    expect(doc.querySelector('img')).toBeNull()
  })

  it('shows unfiled status instead of inventing a collection', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<MediaCard item={{ ...item, collection: '', tags: [] }} onSelect={() => {}} />), 'text/html')
    expect(doc.body.textContent).toContain('Unfiled')
    expect(doc.body.textContent).not.toContain('Sketchbook')
    expect(doc.body.textContent).not.toContain('study')
    expect(doc.querySelectorAll('button')).toHaveLength(1)
  })

  it('escapes artifact names and tags rather than executing authored markup', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<MediaCard item={{ ...item, name: '<script>alert(1)</script>', tags: ['<iframe>'] }} onSelect={() => {}} />), 'text/html')
    expect(doc.querySelector('script')).toBeNull()
    expect(doc.querySelector('iframe')).toBeNull()
    expect(doc.querySelector('button')?.textContent).toBe('<script>alert(1)</script>')
    expect(doc.querySelector('img')?.getAttribute('alt')).toBe('<script>alert(1)</script>')
    expect(doc.body.textContent).toContain('<iframe>')
  })

  it('exposes accessible filters and an honest loading state before HTTP completes', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<LibraryPage />), 'text/html')
    expect(doc.querySelector('section')?.getAttribute('aria-label')).toBe('Media library')
    expect(doc.querySelector('h1')?.textContent).toBe('Media library')
    expect(doc.querySelector('[role="status"]')?.textContent).toBe('Loading media…')
    expect(doc.querySelector('[role="alert"]')).toBeNull()
    expect(doc.querySelector('input[aria-label="Search media"]')?.getAttribute('type')).toBe('search')
    const options = [...doc.querySelectorAll('select[aria-label="Media kind"] option')]
    expect(options.map(option => option.getAttribute('value'))).toEqual(['', 'image', 'video'])
    expect(options.map(option => option.textContent)).toEqual(['Images and videos', 'Images', 'Videos'])
    expect(doc.querySelector('input[aria-label="Filter tag"]')?.getAttribute('list')).toBe('media-tags')
    expect(doc.querySelector('input[aria-label="Filter collection"]')?.getAttribute('list')).toBe('media-collections')
    expect(doc.querySelectorAll('datalist option')).toHaveLength(0)
    expect(doc.querySelector('[aria-label="Media details"]')).toBeNull()
  })

  it('limits import picker to supported image formats and links to sketches', () => {
    const doc = new DOMParser().parseFromString(renderToStaticMarkup(<LibraryPage />), 'text/html')
    const input = doc.querySelector('input[aria-label="Import image"]')
    expect(input?.getAttribute('type')).toBe('file')
    expect(input?.getAttribute('accept')).toBe('image/png,image/jpeg,image/webp')
    expect(input?.hasAttribute('multiple')).toBe(false)
    expect(input?.hasAttribute('disabled')).toBe(false)
    expect(input?.closest('label')?.textContent).toBe('Import image')
    expect(doc.querySelector('a')?.getAttribute('href')).toBe('#/capabilities/media')
    expect(doc.querySelector('a')?.textContent).toBe('Image sketches')
    expect(doc.querySelector('a[download]')).toBeNull()
    expect(doc.body.textContent).not.toContain('0 matching artifacts')
  })
})
