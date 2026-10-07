import { createContext, useContext, useEffect, useId, useReducer, useRef, useState, type FocusEventHandler, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type Ref } from 'react'
import { ChevronDown, X } from 'lucide-react'
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
      <div className="mb-1.5 flex min-w-0 flex-wrap items-start gap-s"><Eyebrow as="span" id={labelId} className="min-w-0 flex-1 [overflow-wrap:anywhere]">{label}</Eyebrow>{right && <div className="min-w-0 max-w-full [overflow-wrap:anywhere]">{right}</div>}</div>
      {children}
      {hint && <p id={hintId} data-type="caption" className="mt-1 break-words text-on-surface-low">{hint}</p>}
    </div>
  </FieldHintProvider></FieldLabelProvider>
}

interface EditGuardProps {
  disabled?: boolean; disabledReason?: string; readOnly?: boolean; readOnlyReason?: string
}
function useEditDescription({ disabled, disabledReason, readOnly, readOnlyReason }: EditGuardProps) {
  const hint = useFieldHintId()
  const identity = useId()
  const reason = disabled ? disabledReason : readOnly ? readOnlyReason : undefined
  const reasonId = reason ? `${identity}-reason` : undefined
  return {
    describedBy: [hint, reasonId].filter(Boolean).join(' ') || undefined,
    explanation: reason ? <span id={reasonId} aria-hidden="true" className="sr-only">{reason}</span> : null,
  }
}

interface TextInputProps extends EditGuardProps {
  value: string; onChange: (value: string) => void; placeholder?: string; autoFocus?: boolean
  onKeyDown?: (event: KeyboardEvent<HTMLInputElement>) => void; onBlur?: FocusEventHandler<HTMLInputElement>
  ref?: Ref<HTMLInputElement>; name?: string; ariaLabel?: string; required?: boolean
  id?: string; size?: FieldSize; surface?: FieldSurface
  type?: 'text' | 'password' | 'number' | 'date' | 'datetime-local' | 'email' | 'url' | 'tel' | 'search' | 'time' | 'month' | 'week'
  mono?: boolean; className?: string; inputMode?: InputHTMLAttributes<HTMLInputElement>['inputMode']
  autoComplete?: InputHTMLAttributes<HTMLInputElement>['autoComplete']; spellCheck?: boolean
  min?: string | number; max?: string | number; step?: string | number; minLength?: number; maxLength?: number; pattern?: string; leadingIcon?: ReactNode; trailingSlot?: ReactNode
}
export function TextInput({ value, onChange, placeholder, autoFocus, onKeyDown, onBlur, ref, name, ariaLabel, required,
  id, size = 'lg', surface = 'container', type, mono, className, inputMode, autoComplete, spellCheck,
  min, max, step, minLength, maxLength, pattern, leadingIcon, trailingSlot,
  disabled, disabledReason, readOnly, readOnlyReason }: TextInputProps) {
  const label = useFieldLabelId()
  const identity = useId()
  const { describedBy, explanation } = useEditDescription({ disabled, disabledReason, readOnly, readOnlyReason })
  const dimensions = sizeTokens[size]
  const input = <input ref={ref} id={id || name || identity} name={name} type={type} value={value} autoFocus={autoFocus} placeholder={placeholder}
    min={min} max={max} step={step} minLength={minLength} maxLength={maxLength} pattern={pattern}
    inputMode={inputMode} autoComplete={autoComplete} spellCheck={spellCheck} required={required} readOnly={readOnly}
    {...fieldNaming(label, ariaLabel, name)} aria-describedby={describedBy} aria-required={required || undefined}
    disabled={disabled} title={disabled ? disabledReason || undefined : undefined}
    onChange={(event) => { if (!disabled && !readOnly) onChange(event.target.value) }} onKeyDown={onKeyDown} onBlur={onBlur}
    data-type={dimensions.role} className={cx(fieldChrome, 'w-full min-w-0 max-w-full', dimensions.height, surfaces[surface],
      leadingIcon && trailingSlot ? 'pl-9 pr-10' : leadingIcon ? 'pl-9 pr-m' : trailingSlot ? 'pl-m pr-10' : 'px-m',
      mono && 'font-mono', disabled && 'opacity-50', className)} />
  if (!leadingIcon && !trailingSlot) return <>{input}{explanation}</>
  return <div className="relative w-full min-w-0 max-w-full">
    {leadingIcon && <span aria-hidden className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-low">{leadingIcon}</span>}
    {input}{explanation}
    {trailingSlot && <span className="absolute right-1.5 top-1/2 -translate-y-1/2">{trailingSlot}</span>}
  </div>
}

interface TextAreaProps extends EditGuardProps {
  value: string; onChange: (value: string) => void; placeholder?: string; rows?: number; mono?: boolean; ariaLabel?: string
  autoFocus?: boolean; id?: string; name?: string; size?: FieldSize; surface?: FieldSurface; className?: string
  required?: boolean; minLength?: number; maxLength?: number; spellCheck?: boolean; wrap?: 'hard' | 'soft' | 'off'
  ref?: Ref<HTMLTextAreaElement>; onBlur?: FocusEventHandler<HTMLTextAreaElement>; onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void
}
export function TextArea({ value, onChange, placeholder, rows = 4, mono, ariaLabel, autoFocus, id, name,
  size = 'lg', surface = 'container', className, required, minLength, maxLength, spellCheck, wrap,
  ref, onBlur, onKeyDown, disabled, disabledReason, readOnly, readOnlyReason }: TextAreaProps) {
  const label = useFieldLabelId()
  const identity = useId()
  const { describedBy, explanation } = useEditDescription({ disabled, disabledReason, readOnly, readOnlyReason })
  return <><textarea ref={ref} id={id || name || identity} name={name} value={value} rows={rows} autoFocus={autoFocus} placeholder={placeholder}
    required={required} minLength={minLength} maxLength={maxLength} spellCheck={spellCheck} wrap={wrap} readOnly={readOnly}
    {...fieldNaming(label, ariaLabel, name)} aria-describedby={describedBy} aria-required={required || undefined}
    disabled={disabled} title={disabled ? disabledReason || undefined : undefined}
    onChange={(event) => { if (!disabled && !readOnly) onChange(event.target.value) }} onBlur={onBlur} onKeyDown={onKeyDown}
    data-type={mono ? 'body-s' : sizeTokens[size].role}
    className={cx(fieldChrome, 'w-full min-w-0 max-w-full resize-y px-m py-2', surfaces[surface], mono && 'font-mono', className)} />{explanation}</>
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
    className={cx(fieldChrome, 'h-8 min-w-0 max-w-full bg-surface-high px-2 text-right tabular-nums', width)} />
}

export function DateInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const label = useFieldLabelId()
  const hint = useFieldHintId()
  const identity = useId()
  return <input id={identity} type="date" value={value} {...fieldNaming(label)} aria-describedby={hint}
    onChange={(event) => onChange(event.target.value)} data-type="body-m" className={cx(fieldChrome, 'h-10 w-full min-w-0 max-w-full bg-surface-container px-m')} />
}

interface SelectProps extends EditGuardProps {
  value: string; onChange: (value: string) => void; options: { value: string; label: string; disabled?: boolean; title?: string }[]
  id?: string; name?: string; ariaLabel?: string; size?: FieldSize; surface?: FieldSurface; required?: boolean
  autoFocus?: boolean; ref?: Ref<HTMLSelectElement>; onBlur?: FocusEventHandler<HTMLSelectElement>; className?: string
}
export function Select({ value, onChange, options, disabled, id, name, ariaLabel, disabledReason,
  readOnly, readOnlyReason, size = 'lg', surface = 'container', required, autoFocus, ref, onBlur, className }: SelectProps) {
  const label = useFieldLabelId()
  const identity = useId()
  const { describedBy, explanation } = useEditDescription({ disabled, disabledReason, readOnly, readOnlyReason })
  return <div className="relative w-full min-w-0 max-w-full"><select ref={ref} id={id || name || identity} name={name} value={value}
    disabled={disabled} required={required} autoFocus={autoFocus} aria-readonly={readOnly || undefined}
    {...fieldNaming(label, ariaLabel, name)} aria-describedby={describedBy} aria-required={required || undefined}
    title={disabled ? disabledReason || undefined : undefined} data-type={sizeTokens[size].role} onBlur={onBlur}
    onMouseDown={(event) => { if (readOnly) event.preventDefault() }}
    onKeyDown={(event) => {
      if (readOnly && !event.ctrlKey && !event.metaKey && !event.altKey &&
        (event.key.length === 1 || ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown', 'Enter'].includes(event.key))) event.preventDefault()
    }}
    onChange={(event) => {
      const next = event.target.value
      if (!disabled && !readOnly && !options.find((option) => option.value === next)?.disabled) onChange(next)
      else event.currentTarget.value = value
    }} className={cx(fieldChrome, sizeTokens[size].height, 'w-full min-w-0 max-w-full appearance-none pl-m pr-8 disabled:opacity-50', surfaces[surface], className)}>
    {options.map(({ value: key, label: text, disabled: unavailable, title }) => <option key={key} value={key} disabled={unavailable} title={title}>{text}</option>)}
  </select>{explanation}<ChevronDown size={16} aria-hidden="true" className={`pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-on-surface-low ${disabled ? 'opacity-50' : ''}`} /></div>
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
  return <div className="flex min-h-10 min-w-0 max-w-full flex-wrap items-center gap-1.5 rounded-md border border-outline-variant/30 bg-surface-container px-2 py-2 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary"
    aria-disabled={disabled || undefined} title={disabled ? disabledReason : undefined}
    onMouseDown={(event) => { if (!disabled && event.target === event.currentTarget) { event.preventDefault(); input.current?.focus() } }}>
    {values.map((value) => <span key={value} data-type="body-s" className="inline-flex min-h-7 min-w-0 max-w-full items-center rounded-lg bg-surface-high pl-2 pr-0 text-on-surface-var">
      <span className="min-w-0 [overflow-wrap:anywhere]">{value}</span><button type="button" aria-label={`Remove ${value}`} onClick={() => remove(value)} disabled={disabled}
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
      }} data-type="body-s" className="min-h-6 min-w-0 basis-20 flex-1 bg-transparent text-on-surface outline-none placeholder:text-on-surface-low disabled:opacity-50" />
    {remaining.length > 0 && <datalist id={listId}>{remaining.map((suggestion) => <option key={suggestion} value={suggestion} />)}</datalist>}
  </div>
}

interface CheckboxProps extends EditGuardProps {
  checked: boolean; onChange: (value: boolean) => void; ariaLabel: string; className?: string
  id?: string; name?: string; value?: string; required?: boolean; ref?: Ref<HTMLInputElement>; onBlur?: FocusEventHandler<HTMLInputElement>
}
export function Checkbox({ checked, onChange, ariaLabel, className, id, name, value, required, ref, onBlur,
  disabled, disabledReason, readOnly, readOnlyReason }: CheckboxProps) {
  const { describedBy, explanation } = useEditDescription({ disabled, disabledReason, readOnly, readOnlyReason })
  return <><input ref={ref} id={id} name={name} value={value} type="checkbox" checked={checked} required={required}
    disabled={disabled} readOnly={readOnly} aria-readonly={readOnly || undefined} aria-required={required || undefined}
    aria-label={ariaLabel} aria-describedby={describedBy} title={disabled ? disabledReason || undefined : undefined} onBlur={onBlur}
    onClick={(event) => event.stopPropagation()}
    onChange={(event) => { event.stopPropagation(); if (!disabled && !readOnly) onChange(event.target.checked); else event.currentTarget.checked = checked }}
    className={cx('size-4 min-h-4 min-w-4 shrink-0 cursor-pointer rounded border-outline-variant accent-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary', className)} />{explanation}</>
}
