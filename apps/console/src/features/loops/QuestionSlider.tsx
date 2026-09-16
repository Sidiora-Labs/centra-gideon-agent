import { useReducer, type Dispatch } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowLeft, ArrowRight, Check, HelpCircle, Sparkles, PencilLine } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { Segmented } from '../../shared/ui/Segmented'
import { Slider } from '../../shared/ui/Slider'
import { Field, TextArea, NumberField } from '../../shared/ui/forms'
import { spring } from '../../shared/theme/motion'
import { accentChip } from '../../shared/theme/accent'
import { initSliderState, sliderReducer, isAnswered, canAdvance, canSubmit, answerRecord, OTHER_CHOICE, type SliderQuestion, type SliderState, type SliderAction } from './sliderState'

type QuestionInput = { question: SliderQuestion; state: SliderState; dispatch: Dispatch<SliderAction> }
function AnswerInput({ question, state, dispatch }: QuestionInput) {
  const answer = state.answers[question.id] ?? ''
  const write = (value: string) => dispatch({ type: 'answer', id: question.id, value })
  const custom = Boolean(state.custom[question.id])
  switch (question.kind) {
    case 'choice': {
      const choices = (question.choices ?? []).filter(Boolean).concat(OTHER_CHOICE)
      const choose = (value: string) => {
        dispatch({ type: 'toggleCustom', id: question.id, on: value === OTHER_CHOICE })
        if (value !== OTHER_CHOICE) write(value)
      }
      return <div className="grid gap-m">
        <Segmented ariaLabel={question.prompt} value={custom ? OTHER_CHOICE : answer} collapse="scroll"
          options={choices.map((key) => ({ key, label: key === OTHER_CHOICE ? 'Other…' : key }))} onChange={choose} />
        {custom && <Field label="Your answer"><TextArea autoFocus value={answer} onChange={write} rows={2} ariaLabel="Your custom answer" placeholder="Describe your answer…" /></Field>}
      </div>
    }
    case 'slider': {
      const { min = 0, max = 10, step } = question
      const numeric = Number(answer)
      const value = answer !== '' && Number.isFinite(numeric) ? numeric : min
      const update = (next: number) => write(String(next))
      return <div className="grid grid-cols-[1fr_auto] items-center gap-l rounded-lg bg-surface-high/40 px-m py-m">
        <Slider value={value} min={min} max={max} step={step} ariaLabel={question.prompt} onChange={update} />
        <NumberField value={value} min={min} max={max} step={step} width="w-20" onChange={update} ariaLabel={`${question.prompt} — value`} />
      </div>
    }
    default: {
      const boundary = question.kind === 'boundary'
      return <Field label={boundary ? 'Hard limits' : 'Your answer'} right={boundary ? <PencilLine size={13} className="text-on-surface-low" /> : undefined}>
        <TextArea autoFocus value={answer} onChange={write} rows={boundary ? 3 : 4} ariaLabel={question.prompt}
          placeholder={boundary ? 'e.g. never touch prod; don’t email anyone' : 'Type your answer…'} />
      </Field>
    }
  }
}

export function QuestionSlider({ questions, seed, onSubmit, onExit, submitLabel = 'Submit answers' }: {
  questions: SliderQuestion[]; seed?: Record<string, string>; onSubmit: (answers: Record<string, string>) => void
  onExit?: () => void; submitLabel?: string
}) {
  const [state, dispatch] = useReducer(sliderReducer, { questions, seed }, (initial) => initSliderState(initial.questions, initial.seed))
  const total = questions.length
  const question = questions[Math.min(state.index, total - 1)]
  if (!question) return null
  const completed = questions.map((entry) => isAnswered(entry, state))
  const last = state.index === total - 1
  const phased = question.phase != null && question.phaseIndex != null && question.phaseCount != null
  const forward = () => dispatch({ type: 'next', total })
  const back = () => state.index === 0 ? onExit?.() : dispatch({ type: 'back' })
  const submit = () => { if (canSubmit(questions, state)) onSubmit(answerRecord(questions, state)) }
  const progress = completed.filter(Boolean).length
  const optionalText = question.kind === 'boundary' ? 'Optional — leave blank if there are no hard limits.' : "Optional — skip it and I'll investigate or assume during the run."

  return <section className="mx-auto grid w-full max-w-[720px] gap-l rounded-xl border border-outline-variant/30 bg-surface p-l">
    <header className="flex items-center justify-between gap-m border-b border-outline-variant/20 pb-m">
      <span data-type="caption" className="text-on-surface-low tabular-nums">Question {state.index + 1} of {total}</span>
      <div aria-hidden className="flex items-center gap-1.5">{questions.map((entry, index) => <span key={entry.id}
        className={`h-1.5 rounded-pill transition-all ${index === state.index ? 'w-5' : 'w-1.5'}`}
        style={{ background: index === state.index ? 'var(--color-primary)' : completed[index] ? 'color-mix(in srgb, var(--color-primary) 45%, transparent)' : 'var(--color-on-surface-low)' }} />)}</div>
    </header>
    <AnimatePresence mode="wait"><motion.div key={question.id} className="grid gap-m"
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} transition={spring.spatialFast}>
      {phased && <span data-type="caption" className="justify-self-start inline-flex items-center gap-1.5 rounded-md px-2.5 py-1" style={accentChip}>
        <Sparkles size={12} /> Phase {question.phaseIndex! + 1} of {question.phaseCount} · {question.phase}
      </span>}
      <div className="flex items-start gap-s"><HelpCircle size={20} className="mt-0.5 shrink-0 text-info" /><h2 data-type="headline-s" className="text-on-surface">{question.prompt}</h2></div>
      <AnswerInput question={question} state={state} dispatch={dispatch} />
      {!question.required && <p data-type="caption" className="text-on-surface-low">{optionalText}</p>}
    </motion.div></AnimatePresence>
    <footer className="flex items-center justify-between gap-m border-t border-outline-variant/30 pt-m">
      <Button variant="ghost" size="sm" onClick={back} disabled={state.index === 0 && !onExit} disabledReason="This is the first question"><ArrowLeft size={15} /> Back</Button>
      {last ? <Button size="sm" onClick={submit} disabled={!canSubmit(questions, state)} disabledReason="Answer the required questions first"><Check size={15} /> {submitLabel} · {progress}/{total}</Button>
        : <div className="flex items-center gap-s">
          {!question.required && <Button variant="ghost" size="sm" onClick={forward}>Skip</Button>}
          <Button size="sm" onClick={forward} disabled={!canAdvance(question, state)} disabledReason="This question is required">Next <ArrowRight size={15} /></Button>
        </div>}
    </footer>
  </section>
}
