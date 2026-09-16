import { useId } from 'react'
import { rangeProgress } from './formState'

export function Slider({ value, onChange, min = 0, max = 10, step = 1, ariaLabel, disabled = false }: {
  value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; ariaLabel?: string; disabled?: boolean
}) {
  const identity = useId()
  const progress = rangeProgress(value, min, max)
  return <input id={identity} type="range" value={value} min={min} max={max} step={step} disabled={disabled} aria-label={ariaLabel}
    onChange={(event) => { if (!disabled) onChange(event.currentTarget.valueAsNumber) }}
    style={{ background: `linear-gradient(to right, color-mix(in srgb, var(--color-primary) 12%, transparent) ${progress}%, transparent ${progress}%)` }}
    className="h-6 w-full rounded-lg accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40" />
}
