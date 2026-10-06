import { useEffect, useState } from 'react'
import { api, type OwnerQuestionAnswer } from '../../shared/data/api'
import type { QuestionSegment } from './chatTypes'

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
  const active = outcome === 'pending' && seg.answerable && verified && !busy && session === seg.session
  const complete = answers.every((answer) => answer.selected.length > 0 || answer.other.trim().length > 0)
  const change = (index: number, patch: Partial<OwnerQuestionAnswer>) => setAnswers((old) => old.map((answer, i) => i === index ? { ...answer, ...patch } : answer))
  const submit = async (skip: boolean) => {
    if (!active || expired() || !session) { if (expired()) setOutcome('expired'); return }
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
    <p className="text-sm font-medium" role="status">{LABELS[outcome]}</p>
    {seg.questions.map((question, index) => <fieldset key={index} disabled={!active} className="space-y-2">
      <legend className="text-sm font-medium">{question.header && <span className="text-on-surface-low">{question.header}: </span>}{question.question}</legend>
      {question.options.map((option, optionIndex) => <label key={optionIndex} className="flex items-start gap-2 rounded-md border border-outline-variant px-3 py-2 text-sm">
        <input type={question.multiSelect ? 'checkbox' : 'radio'} name={`${seg.id}-${index}`} checked={answers[index].selected.includes(optionIndex)}
          onChange={() => change(index, { selected: question.multiSelect ? answers[index].selected.includes(optionIndex) ? answers[index].selected.filter((value) => value !== optionIndex) : [...answers[index].selected, optionIndex] : [optionIndex] })} />
        <span>{option.label}{option.description && <span className="block text-xs text-on-surface-low">{option.description}</span>}</span>
      </label>)}
      {question.free_text !== false && <label className="block text-sm">Other
        <textarea aria-label={`${question.header || question.question} — Other`} value={answers[index].other} maxLength={2000} rows={2}
          className="mt-1 w-full rounded-md border border-outline-variant bg-surface px-2 py-1" onChange={(event) => change(index, { other: event.target.value })} />
      </label>}
    </fieldset>)}
    {outcome === 'pending' && seg.answerable && <div className="flex gap-2">
      <button type="button" disabled={!active || !complete} onClick={() => void submit(false)} className="rounded-md bg-primary px-3 py-2 text-sm text-on-primary disabled:opacity-50">Send answer</button>
      <button type="button" disabled={!active} onClick={() => void submit(true)} className="rounded-md border border-outline-variant px-3 py-2 text-sm disabled:opacity-50">Skip</button>
    </div>}
    {seg.reason && <p className="text-xs text-on-surface-low">{seg.reason}</p>}
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
  </section>
}
