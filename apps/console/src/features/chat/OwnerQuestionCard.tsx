import { useEffect, useState } from 'react'
import { api, type OwnerQuestionAnswer } from '../../shared/data/api'
import type { QuestionSegment } from './chatTypes'
import { Button } from '../../shared/ui/Button'

const LABELS = { answered: 'Answered', skipped: 'Skipped', expired: 'Answer window expired', cancelled: 'Question cancelled', unanswerable: 'Answer channel unavailable', pending: 'Your answer is needed' }

export function OwnerQuestionCard({ seg, session }: { seg: QuestionSegment; session?: string }) {
  const [answers, setAnswers] = useState<OwnerQuestionAnswer[]>(() => seg.questions.map(() => ({ selected: [], other: '' })))
  const [outcome, setOutcome] = useState(seg.outcome)
  const [busy, setBusy] = useState(false)
  const [verified, setVerified] = useState(false)
  const [error, setError] = useState('')
  const expired = () => typeof seg.deadline !== 'number' || !Number.isFinite(seg.deadline) || seg.deadline * 1000 <= Date.now()
  useEffect(() => {
    setOutcome(seg.outcome)
    setVerified(false)
    if (seg.outcome !== 'pending' || !seg.answerable || !session || session !== seg.session) return
    if (expired()) { setOutcome('expired'); return }
    let live = true
    void api.ownerQuestions(session).then(({ questions }) => {
      if (!live) return
      const current = questions.find((question) => question.id === seg.id)
      if (current?.answerable && current.outcome === 'pending') setVerified(true)
      else setOutcome(current?.outcome ?? 'cancelled')
    }).catch(() => { if (live) setError('The answer channel could not be verified. Reopen this chat to retry.') })
    const timer = window.setTimeout(() => { if (live) { setOutcome('expired'); setVerified(false) } }, Math.max(0, seg.deadline! * 1000 - Date.now()))
    return () => { live = false; window.clearTimeout(timer) }
  }, [seg.id, seg.outcome, seg.answerable, seg.deadline, session, seg.session])
  const active = outcome === 'pending' && seg.answerable && verified && !busy && session === seg.session && !expired()
  const unavailableReason = outcome !== 'pending' ? LABELS[outcome]
    : !seg.answerable ? seg.reason || 'This question cannot be answered here.'
    : !session || session !== seg.session ? 'Open the original chat to answer this question.'
    : expired() ? LABELS.expired
    : !verified ? error || 'Verifying the answer channel. Please wait.' : undefined
  const complete = answers.every((answer) => answer.selected.length > 0 || answer.other.trim().length > 0)
  const change = (index: number, patch: Partial<OwnerQuestionAnswer>) => setAnswers((old) => old.map((answer, i) => i === index ? { ...answer, ...patch } : answer))
  const submit = async (skip: boolean) => {
    if (!active || (!skip && !complete) || expired() || !session) { if (expired()) setOutcome('expired'); return }
    setBusy(true); setError('')
    try {
      const { questions } = await api.ownerQuestions(session)
      const current = questions.find((question) => question.id === seg.id)
      if (!current?.answerable || current.outcome !== 'pending' || !current.deadline || current.deadline * 1000 <= Date.now()) {
        setOutcome(current?.outcome === 'pending' ? 'expired' : current?.outcome ?? 'cancelled'); return
      }
      await api.answerOwnerQuestion(session, seg.id, answers, skip)
      setOutcome(skip ? 'skipped' : 'answered'); setVerified(false)
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The answer could not be sent.') }
    finally { setBusy(false) }
  }
  return <section className="rounded-lg border border-outline-variant bg-surface-low p-3 space-y-3" aria-label="Owner question">
    <p data-type="body-s" className="font-medium" role="status">{LABELS[outcome]}</p>
    {seg.questions.map((question, index) => <fieldset key={index} disabled={!active} className="space-y-2">
      <legend data-type="label-m" className="font-medium">{question.header && <span className="text-on-surface-low">{question.header}: </span>}{question.question}</legend>
      {question.options.map((option, optionIndex) => <label data-type="body-s" key={optionIndex} className="flex items-start gap-2 rounded-md border border-outline-variant px-3 py-2 ">
        <input type={question.multiSelect ? 'checkbox' : 'radio'} name={`${seg.id}-${index}`} checked={answers[index].selected.includes(optionIndex)}
          onChange={() => change(index, { selected: question.multiSelect ? answers[index].selected.includes(optionIndex) ? answers[index].selected.filter((value) => value !== optionIndex) : [...answers[index].selected, optionIndex] : [optionIndex] })} />
        <span>{option.label}{option.description && <span data-type="caption" className="block text-on-surface-low">{option.description}</span>}</span>
      </label>)}
      {question.free_text !== false && <label data-type="body-s" className="block ">Other
        <textarea aria-label={`${question.header || question.question} — Other`} value={answers[index].other} maxLength={2000} rows={2}
          className="mt-1 w-full rounded-md border border-outline-variant bg-surface px-2 py-1" onChange={(event) => change(index, { other: event.target.value })} />
      </label>}
    </fieldset>)}
    {outcome === 'pending' && seg.answerable && <div className="flex gap-2">
      <Button size="sm" loading={busy} loadingLabel="Sending answer…" disabled={!active || !complete}
        disabledReason={unavailableReason || (!complete ? 'Answer every question before sending.' : undefined)} onClick={() => void submit(false)}>Send answer</Button>
      <Button size="sm" variant="secondary" loading={busy} loadingLabel="Sending answer…" disabled={!active}
        disabledReason={unavailableReason} onClick={() => void submit(true)}>Skip</Button>
    </div>}
    {seg.reason && <p data-type="body-s" className="text-on-surface-low">{seg.reason}</p>}
    {error && <p data-type="body-s" role="alert" className="text-danger">{error}</p>}
  </section>
}
