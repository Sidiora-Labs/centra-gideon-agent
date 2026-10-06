import type { InboxDraftEvidence } from '../../shared/data/api'

export function DraftNotices({ evidence }: { evidence: InboxDraftEvidence | null }) {
  if (!evidence) return null
  return <div className="my-s grid gap-xs text-on-surface-var" data-type="body-s" aria-live="polite">
    {evidence.question && <p>Before drafting: {evidence.question}</p>}
    {evidence.skipped && <p>No reply was suggested. Your existing draft is unchanged.</p>}
    {evidence.named_notes.map(note => <p key={note.name}>{note.name}: {note.reason}</p>)}
    {evidence.warnings.map(warning => <p key={warning}>{warning}</p>)}
    {evidence.wrote && <p>Draft based on the quoted conversation · {evidence.words} words{evidence.word_limit !== null ? ` · limit ${evidence.word_limit}` : ''}. Review before sending.</p>}
  </div>
}
