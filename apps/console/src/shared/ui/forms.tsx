import { createContext, useContext, useEffect, useId, useReducer, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cx } from './cx'
import { Eyebrow } from './Eyebrow'
import { addChip, commitNumber, createDraft, draftReducer, fieldNaming } from './formState'

const FieldLabelCtx = createContext<string | undefined>(undefined)
const FieldHintCtx = createContext<string | undefined>(undefined)
export const FieldLabelProvider = FieldLabelCtx.Provider
export const FieldHintProvider = FieldHintCtx.Provider
export function useFieldLabelId() { return useContext(FieldLabelCtx) }
export function useFieldHintId() { return useContext(FieldHintCtx) }

type FieldSize = 'sm' | 'md' | 'lg'
type FieldSurface = 'container' | 'high' | 'base'
const sizeTokens = { sm: { height: 'h-8', role: 'body-s' }, md: { height: 'h-9', role: 'body-s' }, lg: { height: 'h-10', role: 'body-m' } }
const surfaces: Record<FieldSurface, string> = { container: 'bg-surface-container', high: 'bg-surface-high', base: 'bg-surface' }
const fieldChrome = 'rounded-md border border-outline-variant/30 text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'

export function FieldError({ children, className }: { children: ReactNode; className?: string }) {
  return <p role="alert" data-type="body-s" className={cx('break-words border-l-2 border-danger/40 pl-s text-danger', className)}>{children}</p>
}

export function Field({ label, hint, right, children }: { label: string; hint?: string; right?: ReactNode; children: ReactNode }) {
  const identity = useId()
  const labelId = `${identity}-label`
  const hintId = hint ? `${identity}-hint` : undefined
  return <FieldLabelProvider value={labelId}><FieldHintProvider value={hintId}>
    <div className="min-w-0">
      <div className="mb-1.5 flex min-w-0 items-center gap-s"><Eyebrow as="span" id={labelId}>{label}</Eyebrow>{right}</div>
      {children}
      {hint && <p id={hintId} data-type="caption" className="mt-1 break-words text-on-surface-low">{hint}</p>}
    </div>
  </FieldHintProvider></FieldLabelProvider>
}

interface TextInputProps {
  value: string; onChange: (value: string) => void; placeholder?: string; autoFocus?: boolean
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void; name?: string; ariaLabel?: string; required?: boolean
  size?: FieldSize; surface?: FieldSurface; type?: 'text' | 'password'; mono?: boolean; leadingIcon?: ReactNode
  disabled?: boolean; disabledReason?: string
}
export function TextInput({ value, onChange, placeholder, autoFocus, onKeyDown, name, ariaLabel, required,
  size = 'lg', surface = 'container', type, mono, leadingIcon, disabled, disabledReason }: TextInputProps) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const identity = useId()
  const dimensions = sizeTokens[size]
  const input = <input id={name || identity} name={name} type={type} value={value} autoFocus={autoFocus} placeholder={placeholder}
    {...fieldNaming(label, ariaLabel, name)} aria-describedby={hint} aria-required={required || undefined}
    disabled={disabled} title={disabled ? disabledReason || undefined : undefined}
    onChange={(event) => { if (!disabled) onChange(event.target.value) }} onKeyDown={onKeyDown}
    data-type={dimensions.role} className={cx(fieldChrome, 'w-full', dimensions.height, surfaces[surface],
      leadingIcon ? 'pl-9 pr-m' : 'px-m', mono && 'font-mono', disabled && 'opacity-50')} />
  if (!leadingIcon) return input
  return <div className="relative w-full">
    <span aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low">{leadingIcon}</span>{input}
  </div>
}

export function TextArea({ value, onChange, placeholder, rows = 4, mono, ariaLabel, autoFocus, size = 'lg', disabled, disabledReason }: {
  value: string; onChange: (value: string) => void; placeholder?: string; rows?: number; mono?: boolean; ariaLabel?: string
  autoFocus?: boolean; size?: FieldSize; disabled?: boolean; disabledReason?: string
}) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const identity = useId()
  return <textarea id={identity} value={value} rows={rows} autoFocus={autoFocus} placeholder={placeholder}
    {...fieldNaming(label, ariaLabel)} aria-describedby={hint} disabled={disabled} title={disabled ? disabledReason || undefined : undefined}
    onChange={(event) => { if (!disabled) onChange(event.target.value) }} data-type={mono ? 'body-s' : sizeTokens[size].role}
    className={cx(fieldChrome, 'w-full resize-y bg-surface-container px-m py-2', mono && 'font-mono')} />
}

export function NumberField({ value, onChange, min, max, step, width = 'w-24', ariaLabel }: {
  value: number; onChange: (value: number) => void; min?: number; max?: number; step?: number; width?: string; ariaLabel?: string
}) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const [draft, dispatch] = useReducer(draftReducer, String(value), createDraft)
  useEffect(() => { dispatch({ type: 'sync', value: String(value) }) }, [value])
  const commit = () => {
    const result = commitNumber(draft.text, value, min, max)
    dispatch({ type: 'edit', text: result.text })
    if (result.changed) onChange(result.value)
  }
  return <input type="number" value={draft.text} min={min} max={max} step={step ?? 1}
    {...fieldNaming(label, ariaLabel)} aria-describedby={hint}
    onChange={(event) => dispatch({ type: 'edit', text: event.target.value })} onBlur={commit}
    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} data-type="body-s"
    className={cx(fieldChrome, 'h-8 bg-surface-high px-2 text-right tabular-nums', width)} />
}

export function DateInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const identity = useId()
  return <input id={identity} type="date" value={value} {...fieldNaming(label)} aria-describedby={hint}
    onChange={(event) => onChange(event.target.value)} data-type="body-m" className={cx(fieldChrome, 'h-10 bg-surface-container px-m')} />
}

interface SelectProps {
  value: string; onChange: (value: string) => void; options: { value: string; label: string; disabled?: boolean; title?: string }[]
  disabled?: boolean; name?: string; ariaLabel?: string; disabledReason?: string; size?: FieldSize; required?: boolean
}
export function Select({ value, onChange, options, disabled, name, ariaLabel, disabledReason, size = 'lg', required }: SelectProps) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const identity = useId()
  return <select id={name || identity} name={name} value={value} disabled={disabled}
    {...fieldNaming(label, ariaLabel, name)} aria-describedby={hint} aria-required={required || undefined}
    title={disabled ? disabledReason || undefined : undefined} data-type={sizeTokens[size].role}
    onChange={(event) => {
      const next = event.target.value
      if (!disabled && !options.find((option) => option.value === next)?.disabled) onChange(next)
    }} className={cx(fieldChrome, sizeTokens[size].height, 'w-full appearance-none bg-surface-container pl-m pr-8 disabled:opacity-50')}>
    {options.map(({ value: key, label: text, disabled: unavailable, title }) => <option key={key} value={key} disabled={unavailable} title={title}>{text}</option>)}
  </select>
}

export { Segmented, type SegOption } from './Segmented'

export function ChipInput({ values, onChange, placeholder, max, suggestions, ariaLabel, disabled, disabledReason }: {
  values: string[]; onChange: (values: string[]) => void; placeholder?: string; max?: number; suggestions?: string[]; ariaLabel?: string
  disabled?: boolean; disabledReason?: string
}) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const listId = useId()
  const input = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState('')
  const commit = () => {
    if (disabled) return
    const next = addChip(values, draft, max)
    setDraft('')
    if (next) onChange(next)
  }
  const remove = (value: string) => {
    if (disabled) return
    onChange(values.filter((item) => item !== value)); input.current?.focus()
  }
  const remaining = suggestions?.filter((suggestion) => !values.includes(suggestion)) ?? []
  return <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-outline-variant/30 bg-surface-container px-2 py-2 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary"
    aria-disabled={disabled || undefined} title={disabled ? disabledReason : undefined}
    onMouseDown={(event) => { if (!disabled && event.target === event.currentTarget) { event.preventDefault(); input.current?.focus() } }}>
    {values.map((value) => <span key={value} data-type="body-s" className="inline-flex h-7 items-center rounded-lg bg-surface-high pl-2 pr-0 text-on-surface-var">
      {value}<button type="button" aria-label={`Remove ${value}`} onClick={() => remove(value)} disabled={disabled}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded-r-lg text-on-surface-low hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:opacity-50"><X size={12} /></button>
    </span>)}
    <input ref={input} value={draft} name={`chip-${listId}`} list={remaining.length ? listId : undefined}
      {...fieldNaming(label, ariaLabel ?? 'Add a tag', undefined, true)} aria-describedby={hint} placeholder={values.length ? '' : placeholder}
      disabled={disabled} onChange={(event) => setDraft(event.target.value)} onBlur={commit}
      onKeyDown={(event) => {
        if (disabled) return
        if (event.nativeEvent.isComposing) return
        if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); commit() }
        else if (event.key === 'Backspace' && draft === '' && values.length) onChange(values.slice(0, -1))
      }} data-type="body-s" className="min-h-6 min-w-[80px] flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low disabled:opacity-50" />
    {remaining.length > 0 && <datalist id={listId}>{remaining.map((suggestion) => <option key={suggestion} value={suggestion} />)}</datalist>}
  </div>
}

export function Checkbox({ checked, onChange, ariaLabel, className }: {
  checked: boolean; onChange: (value: boolean) => void; ariaLabel: string; className?: string
}) {
  return <input type="checkbox" checked={checked} aria-label={ariaLabel} onClick={(event) => event.stopPropagation()}
    onChange={(event) => { event.stopPropagation(); onChange(event.target.checked) }}
    className={cx('size-4 min-h-4 min-w-4 shrink-0 cursor-pointer rounded border-outline-variant accent-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary', className)} />
}
