import { useRef } from 'react'
import { Markdown } from '../../shared/ui/Markdown'
import { CommentLayer } from '../files/comments/CommentLayer'
import type { CommentTarget } from '../../shared/ui/content/commentTarget'

export function PlanningArtifactDoc({ markdown, docId, label, commentTarget }: {
  markdown: string; docId: string; label: string; commentTarget?: CommentTarget
}) {
  const viewport = useRef<HTMLDivElement | null>(null)
  const annotations = commentTarget ? <CommentLayer scrollRef={viewport} docId={docId} docLabel={label}
    content={markdown} onSubmit={(message, docPaths) => commentTarget.submit({ message, docPaths })} /> : null
  return <div ref={viewport} className="relative rounded-lg text-on-surface-var" aria-label={label}>
    <Markdown>{markdown}</Markdown>{annotations}
  </div>
}
