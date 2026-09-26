import { createElement } from 'react'
import type { Artifact } from '../../shared/data/api'
import type { ContentType } from '../../shared/ui/content/contentTypes'
import { artifactTableRows, StructuredArtifactTable } from '../chat/auiStructuredResults'

export function artifactPreviewType(artifact: Artifact, type: ContentType): ContentType {
  if (artifact.kind !== 'json' || !type.preview) return type
  const fallback = type.preview.render
  return { ...type, preview: { ...type.preview, render: (props) => {
    const shown = { ...artifact, content: props.content }
    return artifactTableRows(shown) !== null
      ? <StructuredArtifactTable artifact={shown} />
      : createElement(fallback, props)
  } } }
}
