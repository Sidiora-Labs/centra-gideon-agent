import { memo } from 'react'
import { Box, FolderOpen } from 'lucide-react'
import type { Artifact, ArtifactKind } from '../../shared/data/api'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { Morph } from '../../shared/ui/motion'
import { ArtifactCard } from './ArtifactCard'

const BINARY_ARTIFACT_KINDS = new Set<ArtifactKind>(['image', 'docx', 'xlsx', 'pptx', 'pdf', 'video'])

export const ArtifactGrid = memo(function ArtifactGrid({ artifacts, onOpen, onBrowseFiles, narrowed, kind }: {
  artifacts: Artifact[]
  onOpen: (a: Artifact) => void
  onBrowseFiles: () => void
  narrowed?: boolean
  kind?: ArtifactKind
}) {
  if (!artifacts.length) {
    return narrowed
      ? <EmptyState icon={Box} title="No matching artifacts" hint="Try a different search, kind, or collection." />
      : BINARY_ARTIFACT_KINDS.has(kind ?? 'text')
        ? <EmptyState icon={Box} title="No artifacts" hint="Ask Gideon to create a document, image, or deck, then return here to review its versions."
            action={{ label: 'Browse files', onClick: onBrowseFiles, icon: FolderOpen }} />
        : <EmptyState icon={Box} title="No artifacts" hint="Artifacts are named, versioned snapshots — widgets, docs, images, and files agents produce. Ask the agent to save one, or save a file as an artifact from the Files page."
            action={{ label: 'Browse files', onClick: onBrowseFiles, icon: FolderOpen }} />
  }
  return (
    <div className="grid grid-cols-1 gap-m p-l sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {artifacts.map((a) => (
        <Morph key={a.slug} id={`artifact-${a.slug}`} className="grid">
          <ArtifactCard art={a} onOpen={onOpen} />
        </Morph>
      ))}
    </div>
  )
})
