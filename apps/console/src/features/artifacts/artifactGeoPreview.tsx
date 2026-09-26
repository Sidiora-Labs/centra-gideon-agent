import { KnowledgeGeoMap, type KnowledgeGeoPoint } from '../chat/auiKnowledgeResults'

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function point(value: Record<string, unknown>, index: number): KnowledgeGeoPoint | null {
  const feature = value.type === 'Feature'
  const geometry = feature ? value.geometry : value
  if (!record(geometry) || geometry.type !== 'Point') return null
  const coordinates = geometry.coordinates
  if (!Array.isArray(coordinates) || coordinates.length < 2
    || !coordinates.every(n => typeof n === 'number' && Number.isFinite(n))) return null
  const [longitude, latitude] = coordinates
  if (Math.abs(longitude) > 180 || Math.abs(latitude) > 90) return null
  const properties = feature && record(value.properties) ? value.properties : {}
  const label = typeof properties.name === 'string' && properties.name.trim()
    ? properties.name : `Location ${index + 1}`
  return { id: `location-${index}`, label, longitude, latitude }
}

export function artifactGeoPoints(content: string): KnowledgeGeoPoint[] | null {
  let value: unknown
  try { value = JSON.parse(content) } catch { return null }
  if (!record(value)) return null
  const features = value.type === 'FeatureCollection' ? value.features : [value]
  if (!Array.isArray(features) || features.length === 0) return null
  if (value.type === 'FeatureCollection' && features.some(item => !record(item) || item.type !== 'Feature')) return null
  const points = features.map(point)
  if (points.some(value => value === null)) return null
  return points as KnowledgeGeoPoint[]
}

export function ArtifactGeoPreview({ points }: { points: KnowledgeGeoPoint[] }) {
  return <div className="p-4">
    <KnowledgeGeoMap points={points} />
    <p className="mt-2 text-xs text-on-surface-low">Coordinates shown by longitude and latitude.</p>
  </div>
}
