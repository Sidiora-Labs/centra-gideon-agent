import { memo } from 'react'
import { Box, FolderOpen } from 'lucide-react'
import type { Artifact } from '../../shared/data/api'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { Morph } from '../../shared/ui/motion'
import { ArtifactCard } from './ArtifactCard'

export const ArtifactGrid = memo(function ArtifactGrid({ artifacts, onOpen, onBrowseFiles, narrowed }: {
  artifacts: Artifact[]
  onOpen: (a: Artifact) => void
  onBrowseFiles: () => void
  narrowed?: boolean
}) {
  if (!artifacts.length) {
    return narrowed
      ? <EmptyState icon={Box} title="No matching artifacts" hint="Try a different search, kind, or collection." />
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
