import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, BookOpen } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { PageTitle } from '../../shared/ui/PageTitle'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { IconButton } from '../../shared/ui/IconButton'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, ApiError, type KnowledgeAnnotation, type KnowledgeItem } from '../../shared/data/api'
import { ReadingView } from './ReadingView'
import { readingTimeLabel } from './readingTime'

export function KnowledgeReadingPage({ id, onBack }: { id: string; onBack: () => void }) {
  const [item, setItem] = useState<KnowledgeItem | null>(null)
  const [annotations, setAnnotations] = useState<KnowledgeAnnotation[]>([])
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let alive = true
    setItem(null)
    setMissing(false)
    setError(null)
    api.knowledgeReadingItem(id)
      .then((result) => {
        if (!alive) return
        setItem(result.item)
        setAnnotations(result.annotations)
      })
      .catch((reason) => {
        if (!alive) return
        if (reason instanceof ApiError && reason.status === 404) setMissing(true)
        else setError(reason)
      })
    return () => { alive = false }
  }, [id, reloadKey])

  const reloadAnnotations = useCallback(() => {
    api.knowledgeAnnotations(id).then(setAnnotations).catch(() => {})
  }, [id])

  const title = item?.title || item?.url_title || (missing ? 'Not found' : error ? "Couldn't load" : 'Loading…')
  const readingTime = item ? readingTimeLabel(item) : null

  return (
    <WorkbenchLayout
      scroll={false}
      topBar={
        <TopBar
          keepCornerPadding
          contentAligned
          left={
            <div className="flex min-w-0 items-center gap-s">
              <IconButton icon={ArrowLeft} label="Back to knowledge item" size={40} onClick={onBack}
                className="focus-visible:ring-inset" />
              <BookOpen size={16} className="shrink-0 text-primary" aria-hidden />
              <PageTitle className="truncate">{title}</PageTitle>
            </div>
          }
          right={readingTime
            ? <span data-type="label-s" className="text-on-surface-low">{readingTime}</span>
            : undefined}
        />
      }
    >
      <div className="mx-auto flex h-full min-h-0 w-full flex-col px-l pb-l pt-l" style={{ maxWidth: 'var(--content-width)' }}>
        {error ? (
          <LoadError what="reading view" error={error} onRetry={() => setReloadKey((key) => key + 1)} />
        ) : missing ? (
          <EmptyState icon={BookOpen} title="Knowledge item not found" hint="It may have been removed from your library." />
        ) : item == null ? (
          <ListSkeleton rows={6} what="article" />
        ) : !(item.content || '').trim() ? (
          <EmptyState icon={BookOpen} title="Nothing to read yet" hint="This item does not have a text body." />
        ) : (
          <ReadingView item={item} annotations={annotations} onAnnotationsChanged={reloadAnnotations} />
        )}
      </div>
    </WorkbenchLayout>
  )
}
