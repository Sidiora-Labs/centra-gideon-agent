import { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { fvs } from '../../theme/fontWeight'
import { messageEnter } from '../../theme/motion'
import { clockTime, fullStamp, isoStamp } from '../../data/epoch'
import { FeedbackDialog } from '../../vendor/assistant-ui/elements/feedback-dialog'
import { FileTree, type FileTreeNode } from '../../vendor/assistant-ui/elements/file-tree'
import { ClaudeLogo, GeminiLogo, OpenAILogo } from '../../vendor/assistant-ui/elements/logos'
import { ReviewableDiff } from '../../vendor/assistant-ui/elements/reviewable-diff'
import type { DiffLine } from '../../vendor/assistant-ui/elements/code-diff'
import type { ChatFileChange } from '../../data/api'
import './chatPresentation.css'

export interface MessageFeedback {
  verdict: 'down'
  busy: boolean
  error?: string | null
  onSubmit: (verdict: 'down', reason?: string) => void
  onClose: () => void
}

/** Assistant content and caller-owned actions share the transcript column. */
export function MessageAssistant({ children, actions, timestamp, feedback, model, onFeedbackUp, onFeedbackDown, feedbackBusy, feedbackVerdict, fileChanges, onOpenFile }: {
  children: React.ReactNode
  actions?: React.ReactNode
  timestamp?: string
  feedback?: MessageFeedback
  model?: string
  onFeedbackUp?: () => void
  onFeedbackDown?: () => void
  feedbackBusy?: boolean
  feedbackVerdict?: 'up' | 'down' | null
  fileChanges?: readonly ChatFileChange[]
  onOpenFile?: (path: string) => void
}) {
  const time = clockTime(timestamp)
  const modelLogo = model ? logoForModel(model) : null
  const [note, setNote] = useState('')
  const changedFiles = useMemo(() => (fileChanges ?? []).filter(change => change.path).map(change => ({
    change, complete: !isTruncated(change), diff: diffForChange(change),
  })), [fileChanges])
  useEffect(() => { if (!feedback) setNote('') }, [feedback?.verdict])
  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="gideon-chat-assistant group/msg w-full min-w-0">
      {modelLogo && <div className="mb-1 flex items-center gap-1.5 text-on-surface-low" data-type="caption" title={`Model: ${model}`}>
        {modelLogo}<span>{model}</span>
      </div>}
      <div
        className="gideon-chat-prose max-w-none text-on-surface"
        data-type="body-m"
        style={fvs(400)}
      >
        {children}
      </div>
      {changedFiles.length > 0 && <div className="mt-3 flex flex-col gap-1.5" aria-label="File changes">
        <FileTree nodes={changedFiles.map(({ change, complete, diff }): FileTreeNode => ({
          path: change.path, name: change.path, depth: 0, kind: 'file', snapshotComplete: complete,
          additions: diff?.additions, deletions: diff?.deletions,
        }))} visibleCount={changedFiles.length}
          totalAdditions={changedFiles.every(item => item.diff) ? changedFiles.reduce((sum, item) => sum + item.diff!.additions, 0) : undefined}
          totalDeletions={changedFiles.every(item => item.diff) ? changedFiles.reduce((sum, item) => sum + item.diff!.deletions, 0) : undefined}
          onFileClick={onOpenFile} className="max-w-none" />
        {changedFiles.map(({ change, diff }) => diff && (diff.additions || diff.deletions) && <details key={change.path} className="min-w-0">
          <summary className="cursor-pointer text-sm text-on-surface-var">File changes · {change.path}</summary>
          <ReviewableDiff filename={change.path} mode="applied" hunks={[{ id: change.path, range: diff.range, lines: diff.lines }]} className="mt-1 max-w-none" />
        </details>)}
      </div>}
      {actions}
      {(onFeedbackUp || onFeedbackDown) && <div className="mt-1 flex items-center gap-1.5">
        {onFeedbackUp && <button type="button" aria-label="Mark response helpful" aria-pressed={feedbackVerdict === 'up'}
          disabled={feedbackBusy || feedback?.busy} onClick={onFeedbackUp} className="rounded-md p-2 text-on-surface-low hover:bg-surface-high hover:text-on-surface disabled:opacity-40">
          <ThumbsUp size={14} />
        </button>}
        {onFeedbackDown && <button type="button" aria-label="Mark response unhelpful" aria-pressed={feedbackVerdict === 'down'}
          disabled={feedbackBusy || feedback?.busy} onClick={onFeedbackDown} className="rounded-md p-2 text-on-surface-low hover:bg-surface-high hover:text-on-surface disabled:opacity-40">
          <ThumbsDown size={14} />
        </button>}
      </div>}
      {time && <time dateTime={isoStamp(timestamp)} title={fullStamp(timestamp)} data-type="caption" className="mt-1 block text-on-surface-low">{time}</time>}
      {feedback && (
        <div className="mt-2">
          <fieldset disabled={feedback.busy} className="m-0 border-0 p-0">
            <FeedbackDialog reasons={[]} selected={[]} note={note} sent={false}
              onNoteChange={setNote} onSubmit={feedback.busy ? undefined : () => feedback.onSubmit('down', note.trim() || undefined)} />
          </fieldset>
          {feedback.busy && <p role="status">Saving feedback…</p>}
          {feedback.error && <p role="alert">{feedback.error}</p>}
          <button type="button" onClick={feedback.onClose} disabled={feedback.busy}>Cancel feedback</button>
        </div>
      )}
    </motion.div>
  )
}

function isTruncated(change: ChatFileChange): boolean {
  return change.before.endsWith('\n… [truncated]') || change.after.endsWith('\n… [truncated]')
}

function snapshotLines(text: string): string[] {
  if (!text) return []
  const lines = text.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

function diffForChange(change: ChatFileChange): { lines: DiffLine[]; additions: number; deletions: number; range: string } | null {
  if (isTruncated(change)) return null
  if (change.before && change.after && change.before.endsWith('\n') !== change.after.endsWith('\n')) return null
  const before = snapshotLines(change.before)
  const after = snapshotLines(change.after)
  if (before.length + after.length > 2_000 || before.length * after.length > 250_000) return null
  const lengths = Array.from({ length: before.length + 1 }, () => new Uint16Array(after.length + 1))
  for (let i = before.length - 1; i >= 0; i--) {
    for (let j = after.length - 1; j >= 0; j--) {
      lengths[i][j] = before[i] === after[j] ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1])
    }
  }
  const lines: DiffLine[] = []
  let i = 0, j = 0, additions = 0, deletions = 0
  while (i < before.length || j < after.length) {
    if (i < before.length && j < after.length && before[i] === after[j]) {
      lines.push({ kind: 'context', text: before[i] }); i++; j++
    } else if (i < before.length && (j === after.length || lengths[i + 1][j] >= lengths[i][j + 1])) {
      lines.push({ kind: 'removed', text: before[i++] }); deletions++
    } else {
      lines.push({ kind: 'added', text: after[j++] }); additions++
    }
  }
  return { lines, additions, deletions,
    range: `@@ -${before.length ? 1 : 0},${before.length} +${after.length ? 1 : 0},${after.length} @@` }
}

function logoForModel(model: string) {
  if (/claude|anthropic/i.test(model)) return <ClaudeLogo className="size-4" />
  if (/gemini|google/i.test(model)) return <GeminiLogo className="size-4" />
  if (/^(gpt|chatgpt|openai|o[1-9](?:\b|-))/i.test(model)) return <OpenAILogo className="size-4" />
  return null
}
