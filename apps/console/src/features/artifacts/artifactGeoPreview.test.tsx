import { createElement } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../shared/data/api'
import type { ContentType } from '../../shared/ui/content/contentTypes'
import { TextPreview } from '../../shared/ui/content/renderers'
import { artifactGeoPoints } from './artifactGeoPreview'
import { artifactPreviewType } from './artifactTablePreview'

const point = (coordinates: unknown) => ({ type: 'Point', coordinates })
const feature = (coordinates: unknown, properties: unknown = null) => ({
  type: 'Feature', geometry: point(coordinates), properties,
})
const artifact: Artifact = {
  slug: 'locations', name: 'Locations', kind: 'json', source: 'manual',
  description: '', tags: [], version: 2, created_at: '', updated_at: '',
  content: JSON.stringify(point([0, 0])), events: [], source_path: '', readonly: false,
}
const type: ContentType = {
  id: 'json', label: 'JSON', icon: FileText, tone: 'gray', kinds: ['json'],
  edit: { language: 'json', split: true }, preview: { render: TextPreview },
}
function preview(content: string) {
  const resolved = artifactPreviewType(artifact, type)
  return createElement(resolved.preview!.render, { content, title: artifact.name, mode: 'dark' })
}

describe('GeoJSON artifact coordinates', () => {
  it('keeps GeoJSON longitude first and zero coordinates measurable', () => {
    expect(artifactGeoPoints(JSON.stringify(point([0, 0])))).toEqual([
      { id: 'location-0', label: 'Location 1', longitude: 0, latitude: 0 },
    ])
    expect(artifactGeoPoints(JSON.stringify(point([13.4, 52.5, 37])))).toEqual([
      { id: 'location-0', label: 'Location 1', longitude: 13.4, latitude: 52.5 },
    ])
  })

  it('accepts exact coordinate bounds without clamping', () => {
    for (const coordinates of [[-180, -90], [180, 90]]) {
      const result = artifactGeoPoints(JSON.stringify(point(coordinates)))!
      expect([result[0].longitude, result[0].latitude]).toEqual(coordinates)
    }
  })

  it('uses actual feature names and stable distinct identities for repeated feature ids', () => {
    const features = [feature([10, 20], { name: 'First site' }), feature([11, 21], { name: 'Second site' })]
      .map(value => ({ ...value, id: 'shared-id' }))
    expect(artifactGeoPoints(JSON.stringify({ type: 'FeatureCollection', features }))).toEqual([
      { id: 'location-0', label: 'First site', longitude: 10, latitude: 20 },
      { id: 'location-1', label: 'Second site', longitude: 11, latitude: 21 },
    ])
  })

  it.each([null, [], {}, { name: '' }, { name: '   ' }, { name: 42 }])('falls back to position for unnamed feature %j', properties => {
    expect(artifactGeoPoints(JSON.stringify(feature([12, 34], properties)))?.[0].label).toBe('Location 1')
  })

  it.each([
    'not JSON', 'null', '[]', '7', '"Point"', '{}',
    JSON.stringify({ type: 'Point' }),
    JSON.stringify(point(null)), JSON.stringify(point([1])), JSON.stringify(point(['1', 2])),
    JSON.stringify(point([1, null])), JSON.stringify(point([181, 0])), JSON.stringify(point([0, -91])),
    '{"type":"Point","coordinates":[1e999,0]}',
    JSON.stringify({ type: 'Feature', geometry: null }),
    JSON.stringify({ type: 'Feature', geometry: [] }),
    JSON.stringify({ type: 'LineString', coordinates: [[0, 0], [1, 1]] }),
    JSON.stringify({ type: 'FeatureCollection' }),
    JSON.stringify({ type: 'FeatureCollection', features: {} }),
    JSON.stringify({ type: 'FeatureCollection', features: [] }),
    JSON.stringify({ type: 'FeatureCollection', features: [null] }),
    JSON.stringify({ type: 'FeatureCollection', features: [point([0, 0])] }),
    JSON.stringify({ type: 'FeatureCollection', features: [feature([0, 0]), feature([0, 100])] }),
  ])('preserves text fallback for unsupported or invalid input %s', content => {
    expect(artifactGeoPoints(content)).toBeNull()
  })

  it('renders the real adopted map and readable coordinates without inactive controls', () => {
    const { container } = render(preview(JSON.stringify(feature([13.4, 52.5], { name: 'Berlin' }))))
    expect(container.querySelectorAll('[data-slot="geo-map"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-slot="map-answer"]')).toHaveLength(1)
    expect(screen.getByText('52.5, 13.4')).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Berlin' })).toBeTruthy()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('uses the displayed historical content rather than the latest artifact', () => {
    render(preview(JSON.stringify(feature([-74, 40.7], { name: 'Historical location' }))))
    expect(screen.getByText('40.7, -74')).toBeTruthy()
    expect(screen.queryByText('0, 0')).toBeNull()
  })

  it('preserves the editor and split contract when enabling map preview', () => {
    const resolved = artifactPreviewType(artifact, type)
    expect(resolved.edit).toBe(type.edit)
    expect(resolved.id).toBe(type.id)
  })

  it('retains ordinary tabular JSON preview', () => {
    render(preview('[{"name":"Actual record","count":0}]'))
    expect(screen.getByRole('table')).toBeTruthy()
    expect(screen.getByRole('cell', { name: 'Actual record' })).toBeTruthy()
    expect(screen.getByRole('cell', { name: '0' })).toBeTruthy()
  })

  it('does not silently hide invalid members of a collection', () => {
    const content = JSON.stringify({ type: 'FeatureCollection', features: [feature([0, 0]), feature([200, 100])] })
    const { container } = render(preview(content))
    expect(container.querySelector('[data-slot="geo-map"]')).toBeNull()
    expect(container.textContent).toContain('200')
  })

  it('renders user labels as text rather than markup', () => {
    const name = '<img src=x onerror=alert(1)>'
    const { container } = render(preview(JSON.stringify(feature([0, 0], { name }))))
    expect(screen.getByText(name)).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
  })
})
