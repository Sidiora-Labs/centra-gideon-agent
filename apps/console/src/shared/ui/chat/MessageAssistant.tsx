import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { fvs } from '../../theme/fontWeight'
import { messageEnter } from '../../theme/motion'
import { clockTime, fullStamp, isoStamp } from '../../data/epoch'
import { FeedbackDialog } from '../../vendor/assistant-ui/elements/feedback-dialog'
import { ClaudeLogo, GeminiLogo, OpenAILogo } from '../../vendor/assistant-ui/elements/logos'
import './chatPresentation.css'

export interface MessageFeedback {
  verdict: 'down'
  busy: boolean
  error?: string | null
  onSubmit: (verdict: 'down', reason?: string) => void
  onClose: () => void
}

/** Assistant content and caller-owned actions share the transcript column. */
export function MessageAssistant({ children, actions, timestamp, feedback, model, onFeedbackUp, onFeedbackDown, feedbackBusy, feedbackVerdict }: {
  children: React.ReactNode
  actions?: React.ReactNode
  timestamp?: string
  feedback?: MessageFeedback
  model?: string
  onFeedbackUp?: () => void
  onFeedbackDown?: () => void
  feedbackBusy?: boolean
  feedbackVerdict?: 'up' | 'down' | null
}) {
  const time = clockTime(timestamp)
  const modelLogo = model ? logoForModel(model) : null
  const [note, setNote] = useState('')
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

function logoForModel(model: string) {
  if (/claude|anthropic/i.test(model)) return <ClaudeLogo className="size-4" />
  if (/gemini|google/i.test(model)) return <GeminiLogo className="size-4" />
  if (/^(gpt|chatgpt|openai|o[1-9](?:\b|-))/i.test(model)) return <OpenAILogo className="size-4" />
  return null
}
