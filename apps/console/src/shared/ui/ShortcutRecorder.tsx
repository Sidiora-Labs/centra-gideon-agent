import { useReducer, type KeyboardEvent } from 'react'
import { useFieldHintId } from './forms'
import { recordingReducer } from './interactionState'

export function ShortcutRecorder({ value, format, parse, onRecord, label }: {
  value: string
  format: (chord: string) => string
  parse: (event: KeyboardEvent) => string
  onRecord: (chord: string) => void
  label: string
}) {
  const hintId = useFieldHintId()
  const [phase, dispatch] = useReducer(recordingReducer, 'idle')
  const listening = phase === 'listening'
  const formatted = format(value)
  const capture = (event: KeyboardEvent) => {
    if (!listening) return
    event.preventDefault()
    event.stopPropagation()
    if (event.key === 'Escape') {
      dispatch('cancel')
      return
    }
    const chord = parse(event)
    if (chord === '') return
    dispatch('commit')
    onRecord(chord)
  }
  return <button type="button" onClick={() => dispatch('arm')} onBlur={() => dispatch('cancel')} onKeyDown={capture}
    aria-label={listening ? `Press the new ${label.toLowerCase()}, or Escape to cancel` : `${label}: ${formatted} — activate to change`}
    aria-describedby={hintId} data-type="body-s"
    className={`inline-flex h-9 min-w-32 items-center justify-center gap-2 rounded-lg border px-3 font-mono transition-colors ${listening
      ? 'border-primary bg-primary/10 text-on-surface ring-2 ring-inset ring-primary'
      : 'border-outline-variant/50 bg-surface-high text-on-surface hover:bg-surface-highest'}`}>
    <span aria-hidden className={`size-1.5 rounded-full ${listening ? 'bg-primary' : 'bg-outline-variant'}`} />
    {listening ? 'Press keys…' : formatted}
  </button>
}
