import type { SessionSearchAnswer } from '../../shared/data/api'
import { QuietButton } from '../../shared/ui/QuietButton'

export function ChatSearchCoverage({ query, answer, pending, error, onSearchAll }: {
  query: string
  answer: SessionSearchAnswer | null
  pending: boolean
  error: boolean
  onSearchAll: () => void
}) {
  if (query.trim().length < 2) return null
  const message = pending ? 'Searching chat contents…'
    : error ? 'Chat contents could not be searched. Title matches are still shown.'
    : !answer ? 'Chat contents have not been searched yet.'
    : answer.complete ? `Searched all ${answer.searched.of} chats.`
    : `Searched ${answer.searched.chats} of ${answer.searched.of} chats. Results may be incomplete.`
  return <div className="mt-1 flex flex-wrap items-center gap-2 text-[0.75rem] text-on-surface-low">
    <span role="status" aria-live="polite">{message}{!pending && answer?.index?.building ? ' Search index is still being built.' : ''}</span>
    {!pending && (error || (answer && !answer.complete)) &&
      <QuietButton onClick={onSearchAll}>Search all chats</QuietButton>}
  </div>
}
