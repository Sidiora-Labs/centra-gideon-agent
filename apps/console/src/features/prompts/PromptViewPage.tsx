import { usePromptRecord } from './promptLibraryState'
import { ArrowLeft, FileText, Puzzle, Loader2 } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { PageTitle } from '../../shared/ui/PageTitle'
import { type PromptItem, type PromptSnippet } from '../../shared/data/api'
import { sourceTone } from './promptMeta'
import { PromptDetail } from './PromptDetail'
import { SnippetDetail } from './SnippetDetail'
import { useEditFlag, type RouteProps } from '../../app/shell/useQueryState'

export function PromptViewPage({ kind, name, onBack, navigate, query, setQuery }: {
  kind: 'system' | 'user' | 'snippets'
  name: string
  onBack: () => void
} & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const [editing, setEditing] = useEditFlag(query, setQuery)
  const isSnippet = kind === 'snippets'
  const { record: loaded, refresh } = usePromptRecord(isSnippet, name)
  const tone = sourceTone(typeof loaded === 'object' && loaded ? loaded.source : undefined)

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s">
        <IconButton icon={ArrowLeft} label="Back to prompts" size={40} onClick={onBack} />
        {isSnippet ? <Puzzle size={18} style={{ color: tone }} /> : <FileText size={18} style={{ color: tone }} />}
        <PageTitle className="truncate">{name}</PageTitle>
      </div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto grid gap-l px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {loaded === null ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
          ) : loaded === 'missing' ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center text-on-surface-low">
              {isSnippet ? <Puzzle size={26} className="opacity-40" /> : <FileText size={26} className="opacity-40" />}
              <p data-type="body-m" className="text-on-surface">This {isSnippet ? 'snippet' : 'prompt'} no longer exists</p>
              <p data-type="body-s">It may have been deleted, or the link is stale.</p>
            </div>
          ) : isSnippet ? (
            <SnippetDetail snippet={loaded as PromptSnippet} editing={editing} onEditingChange={setEditing} onSaved={refresh} onDeleted={onBack} />
          ) : (
            <PromptDetail prompt={loaded as PromptItem} editing={editing} onEditingChange={setEditing} onSaved={refresh} onDeleted={onBack} onNavigate={navigate} />
          )}
        </div>
      </div>
    </div>
  )
}
