import { useCallback, useMemo, useState } from 'react'
import { AlertTriangle, Check, CheckCircle2, X } from 'lucide-react'
import { api, type ChatSessionSummary, type InboxItem, type PendingApproval } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { rowSubject } from '../../shared/data/rowSubject'
import { Button } from '../../shared/ui/Button'
import { TextLink } from '../../shared/ui/TextLink'
import { LANES, toLanes, type Lane } from '../../shared/data/attentionLanes'
import { BUSY_REASON } from '../../shared/ui/unavailable'


export const LANE_REFS: Record<Lane, string> = {
  'needs-approval': 'core:lane-needs-approval',
  'your-turn': 'core:lane-your-turn',
  working: 'core:lane-working',
  idle: 'core:lane-idle',
}

export const MISSION_CONTROL_VIEW_ID = 'mission-control'

export function laneForRef(ref: string): Lane | null {
  return LANES.find((l) => LANE_REFS[l] === ref) ?? null
}

const LANE_LABEL: Record<Lane, string> = {
  'needs-approval': 'Needs approval',
  'your-turn': 'Your turn',
  working: 'Working',
  idle: 'Idle',
}
const LANE_EMPTY: Record<Lane, string> = {
  'needs-approval': 'Nothing is waiting on your approval.',
  'your-turn': 'Nothing is waiting on an answer from you.',
  working: 'Nothing is running right now.',
  idle: 'Nothing is idle.',
}

const ATTENTION_KEY = 'dashboard:mission-control'

interface Attention {
  items: InboxItem[]
  approvals: PendingApproval[]
  activity: SessionActivity[]
}

export interface SessionActivity {
  key: string
  title: string
  running: boolean
  stopping: boolean
  pending_approval: boolean
}

function activityOf(s: ChatSessionSummary): SessionActivity {
  const wire = s as ChatSessionSummary & { stopping?: boolean; pending_approval?: boolean }
  return {
    key: s.key,
    title: s.title,
    running: Boolean(s.running),
    stopping: Boolean(wire.stopping),
    pending_approval: Boolean(wire.pending_approval),
  }
}

async function readAttention(): Promise<Attention> {
  const [items, approvals, sessions] = await Promise.all([
    api.inboxPending(),
    api.approvals(),
    api.chatSessions(),
  ])
  return { items, approvals, activity: sessions.map(activityOf) }
}

export interface CardQuestion {
  runId: string
  nodeId: string
  resumeToken: string
  prompt: string
  choices: string[]
}

export function questionOf(item: InboxItem | null | undefined): CardQuestion | null {
  const refs = item?.refs
  if (!refs || typeof refs !== 'object') return null
  const payload = refs.needs_input
  if (!payload || typeof payload !== 'object') return null
  const runId = String(payload.run_id ?? refs.workflow ?? '')
  if (!runId) return null
  const choices = (Array.isArray(payload.choices) ? payload.choices : [])
    .filter((c: unknown): c is string => typeof c === 'string' && c.length > 0)
  return {
    runId,
    nodeId: String(payload.node_id ?? refs.workflow_node ?? ''),
    resumeToken: String(payload.resume_token ?? refs.resume_token ?? ''),
    prompt: String(payload.blocker ?? item?.message ?? ''),
    choices,
  }
}

type Outcome =
  | { state: 'busy' }
  | { state: 'done'; text: string }
  | { state: 'failed'; text: string }

function failureText(verb: string, err: unknown): string {
  const detail = err instanceof Error && err.message ? err.message : String(err ?? 'unknown error')
  return `Could not ${verb}: ${detail}. Nothing was recorded — it still needs you, so try again.`
}

export function MissionControl() {
  const { data, error, loading, refresh } = useQuery<Attention>(ATTENTION_KEY, readAttention)
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({})

  const items = data?.items ?? []
  const approvals = data?.approvals ?? []
  const activity = data?.activity ?? []
  const lanes = useMemo(() => toLanes(items, approvals, activity), [items, approvals, activity])

  const mark = useCallback((id: string, o: Outcome) => {
    setOutcomes((prev) => ({ ...prev, [id]: o }))
  }, [])

  const resolve = useCallback(
    (cardId: string, approvalId: string, action: 'approve' | 'reject') => {
      mark(cardId, { state: 'busy' })
      api
        .resolveApproval(approvalId, action)
        .then(() => {
          mark(cardId, { state: 'done', text: action === 'approve' ? 'Approved.' : 'Rejected.' })
          refresh()
        })
        .catch((err) => mark(cardId, { state: 'failed', text: failureText(`${action} this`, err) }))
    },
    [mark, refresh],
  )

  const answer = useCallback(
    (cardId: string, q: CardQuestion, choice: string) => {
      mark(cardId, { state: 'busy' })
      api
        .resumeWorkflowRun(q.runId, { answer: choice, resume_token: q.resumeToken || undefined })
        .then(() => {
          mark(cardId, { state: 'done', text: `Answered “${choice}” — the run is moving again.` })
          refresh()
        })
        .catch((err) => mark(cardId, { state: 'failed', text: failureText('send that answer', err) }))
    },
    [mark, refresh],
  )

  return (
    <section aria-labelledby="mission-control-title" className="flex min-w-0 flex-col gap-l">
      <div className="flex min-w-0 flex-col gap-xs">
        <h2 id="mission-control-title" data-type="title-m" className="text-on-surface">
          Mission Control
        </h2>
        <p data-type="body-s" className="text-on-surface-low">
          Everything wanting your attention, in the order it wants it. Approve, reject, and answer
          from here — you do not have to open the run.
        </p>
      </div>

      {
}
      {error ? (
        <div
          role="alert"
          className="flex min-w-0 items-start gap-s rounded-lg border border-error/40 bg-error/10 p-s text-on-surface"
        >
          <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
          <div className="flex min-w-0 flex-col gap-xs">
            <p data-type="body-s">
              {failureText('load what needs your attention', error)}
            </p>
            <Button size="xs" variant="ghost-accent" onClick={refresh}>
              Try again
            </Button>
          </div>
        </div>
      ) : null}

      {LANES.map((lane) => (
        <AttentionLaneSection
          key={lane}
          lane={lane}
          cards={lanes[lane] ?? []}
          loading={loading}
          outcomes={outcomes}
          onResolve={resolve}
          onAnswer={answer}
        />
      ))}
    </section>
  )
}

type ConsumedCard = {
  id: string
  title?: string
  detail?: string
  approval?: PendingApproval | null
  item?: InboxItem | null
}

function AttentionLaneSection({
  lane,
  cards,
  loading,
  outcomes,
  onResolve,
  onAnswer,
}: {
  lane: Lane
  cards: ConsumedCard[]
  loading: boolean
  outcomes: Record<string, Outcome>
  onResolve: (cardId: string, approvalId: string, action: 'approve' | 'reject') => void
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  const headingId = `mission-control-lane-${lane}`
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        {
}
        <h2 id={headingId} data-type="label-l" className="text-on-surface-var">
          {LANE_LABEL[lane]}
        </h2>
        {
}
        <span data-type="label-s" className="text-on-surface-low">
          {cards.length}
        </span>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {cards.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          {loading ? 'Loading…' : LANE_EMPTY[lane]}
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-s">
          {cards.map((c) => (
            <li key={c.id} className="min-w-0">
              <AttentionCard
                card={c}
                outcome={outcomes[c.id]}
                onResolve={onResolve}
                onAnswer={onAnswer}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function AttentionCard({
  card,
  outcome,
  onResolve,
  onAnswer,
}: {
  card: ConsumedCard
  outcome: Outcome | undefined
  onResolve: (cardId: string, approvalId: string, action: 'approve' | 'reject') => void
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  const approval = card.approval ?? null
  const question = questionOf(card.item)
  const subject = rowSubject([
    card.title,
    approval?.tool,
    approval?.session,
    card.item?.channel_name,
    card.detail,
  ])
  const resolved = outcome?.state === 'done'
  const busy = outcome?.state === 'busy'

  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s">
      <p data-type="label-m" className="min-w-0 truncate text-on-surface-var">
        {card.title || subject || card.id}
      </p>
      {card.detail ? (
        <p data-type="body-s" className="min-w-0 text-on-surface-low">
          {card.detail}
        </p>
      ) : null}
      {question?.prompt ? (
        <p data-type="body-s" className="min-w-0 text-on-surface">
          {question.prompt}
        </p>
      ) : null}

      {
}
      {outcome?.state === 'done' ? (
        <p
          role="status"
          data-type="body-s"
          className="flex min-w-0 items-center gap-xs text-on-surface-var"
        >
          <CheckCircle2 size={14} className="shrink-0 text-success" aria-hidden="true" />
          {outcome.text}
        </p>
      ) : null}
      {outcome?.state === 'failed' ? (
        <p role="alert" data-type="body-s" className="flex min-w-0 items-start gap-xs text-on-surface">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-error" aria-hidden="true" />
          {outcome.text}
        </p>
      ) : null}

      {
}
      {resolved ? null : (
        <div className="flex min-w-0 flex-wrap items-center gap-xs">
          {approval ? (
            <>
              <Button
                size="xs"
                variant="primary"
                loading={busy}
                disabled={busy}
                ariaLabel={`Approve ${subject}`}
                onClick={() => onResolve(card.id, approval.id, 'approve')}
              >
                <Check size={13} aria-hidden="true" /> Approve
              </Button>
              <Button
                size="xs"
                variant="secondary"
                disabled={busy} disabledReason={BUSY_REASON}
                ariaLabel={`Reject ${subject}`}
                onClick={() => onResolve(card.id, approval.id, 'reject')}
              >
                <X size={13} aria-hidden="true" /> Reject
              </Button>
            </>
          ) : null}

          {question ? (
            <QuestionActions
              card={card}
              question={question}
              subject={subject}
              busy={busy}
              onAnswer={onAnswer}
            />
          ) : null}
        </div>
      )}
    </div>
  )
}

function QuestionActions({
  card,
  question,
  subject,
  busy,
  onAnswer,
}: {
  card: ConsumedCard
  question: CardQuestion
  subject: string
  busy: boolean
  onAnswer: (cardId: string, q: CardQuestion, choice: string) => void
}) {
  if (question.choices.length === 0) {
    return (
      <p data-type="body-s" className="text-on-surface-low">
        This question has no preset options —{' '}
        <TextLink href={`#/workflows/runs/${question.runId}`} ink="emphasis" aria-label={`Open the run: ${subject}`}>
          open the run
        </TextLink>{' '}
        to answer it in your own words.
      </p>
    )
  }
  return (
    <>
      {question.choices.map((choice) => (
        <Button
          key={choice}
          size="xs"
          variant="tonal"
          disabled={busy} disabledReason={BUSY_REASON}
          ariaLabel={`Answer ${subject} — ${choice}`}
          onClick={() => onAnswer(card.id, question, choice)}
        >
          {choice}
        </Button>
      ))}
    </>
  )
}
