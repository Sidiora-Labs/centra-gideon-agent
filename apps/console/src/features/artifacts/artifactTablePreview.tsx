import { createElement } from 'react'
import type { Artifact } from '../../shared/data/api'
import type { ContentType, PreviewProps } from '../../shared/ui/content/contentTypes'
import { artifactTableRows, StructuredArtifactTable } from '../chat/auiStructuredResults'
import { artifactGeoPoints, ArtifactGeoPreview } from './artifactGeoPreview'

export function artifactPreviewType(artifact: Artifact, type: ContentType): ContentType {
  if (artifact.kind !== 'json' || !type.preview) return type
  const fallback = type.preview.render
  return { ...type, preview: { ...type.preview, render: (props: PreviewProps) => {
    const shown = { ...artifact, content: props.content }
    const points = artifactGeoPoints(props.content)
    if (points) return <ArtifactGeoPreview points={points} />
    return artifactTableRows(shown) !== null
      ? <div className="p-4"><StructuredArtifactTable artifact={shown} showTitle={false} /></div>
      : createElement(fallback, props)
  } } }
}
