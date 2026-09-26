import { useEffect, useId, useReducer, useRef } from 'react'
import { createPortal } from 'react-dom'
import { motion, useIsPresent } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import { spring, physics, expr, useReducedMotion } from '../../theme/motion'
import { captureFocus, FocusScope } from '../focusNavigation'
import { useDismissKey } from '../overlayInteraction'
import { unavailableWhen } from '../unavailable'
import { dialogErrors, dialogReducer, dialogState } from './dialogState'
import type { DialogField, DialogResult, DialogRequest } from './dialogStore'

export function DialogShell({ request, onClose, active = true }: {
  request: DialogRequest; onClose: (result: DialogResult) => void; active?: boolean
}) {
  const { kind, title, body, tone, confirmLabel, cancelLabel, fields = [], icon: Icon } = request
  const danger = tone === 'danger'
  const isPrompt = kind === 'prompt'
  const isAlert = kind === 'alert'
  const trapRef = useRef<HTMLDivElement>(null)
  const origin = useRef(captureFocus())
  const present = useIsPresent()
  const ownsInteraction = active && present
  useEffect(() => {
    if (!ownsInteraction || !trapRef.current) return
    return new FocusScope(origin.current).attach(trapRef.current)
  }, [ownsInteraction])
  const identity = useId()
  const reduced = useReducedMotion()
  const settled = useRef(false)
  const [state, dispatch] = useReducer(dialogReducer, fields, dialogState)
  const finish = (result: DialogResult) => {
    if (settled.current || !ownsInteraction) return
    settled.current = true
    onClose(result)
  }
  const cancel = () => finish(isPrompt ? null : false)
  const submit = () => {
    if (!ownsInteraction || settled.current) return
    if (!isPrompt) { finish(true); return }
    const errors = dialogErrors(fields, state.values)
    dispatch({ type: 'errors', errors })
    if (Object.keys(errors).length === 0) finish({ ...state.values })
  }
  useDismissKey('Escape', cancel, ownsInteraction ? 120 : -1)
  useEffect(() => {
    if (!ownsInteraction || danger || !isPrompt) return
    const keyboard = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.key !== 'Enter' || !(event.target instanceof HTMLInputElement)
        || !trapRef.current?.contains(event.target)) return
      event.preventDefault()
      event.stopImmediatePropagation()
      submit()
    }
    window.addEventListener('keydown', keyboard)
    return () => window.removeEventListener('keydown', keyboard)
  })
  const canSubmit = fields.every((field) => !field.required || !!state.values[field.name]?.trim())
  const resting = { opacity: 1, scale: 1, y: 0 }
  const hidden = { opacity: 0, scale: reduced ? 1 : 0.98, y: reduced ? 0 : expr(12, 0.3) }
  return createPortal(
    <motion.div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-l sm:p-2xl" inert={!ownsInteraction} aria-hidden={!ownsInteraction || undefined}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" onClick={cancel} aria-hidden="true" />
      <motion.div ref={trapRef} role={isAlert || danger ? 'alertdialog' : 'dialog'} aria-modal="true"
        aria-label={typeof title === 'string' ? title : undefined}
        aria-describedby={body ? `${identity}-body` : undefined}
        className="relative flex max-h-full w-full max-w-[420px] flex-col overflow-hidden rounded-2xl border border-outline-variant/50 bg-surface shadow-sheet"
        initial={hidden} animate={resting} exit={hidden} transition={reduced ? spring.effects : physics.fluid}>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <header className="flex items-start gap-m border-b border-outline-variant/30 bg-surface-high/40 px-l py-l">
          {(danger || Icon) && <span className={`mt-0.5 shrink-0 ${danger ? 'text-danger' : 'text-primary'}`}>
            {Icon ? <Icon size={18} /> : <AlertTriangle size={18} />}
          </span>}
          <div className="min-w-0 flex-1">
            <h2 data-type="title-l" className="break-words text-on-surface">{title}</h2>
            {body && <div id={`${identity}-body`} data-type="body-s" className="mt-2 whitespace-pre-line text-on-surface-var">{body}</div>}
          </div>
        </header>
        {isPrompt && fields.length > 0 && <div className="flex flex-col gap-m px-l py-l">
          {fields.map((field, index) => <PromptField key={field.name} field={field} value={state.values[field.name] ?? ''}
            error={state.errors[field.name]} autoFocus={index === 0} id={`${identity}-field-${index}`}
            onChange={(value) => dispatch({ type: 'edit', name: field.name, value })} />)}
        </div>}
        </div>
        <footer className="flex shrink-0 flex-wrap justify-end gap-s border-t border-outline-variant/30 px-l py-l">
          {!isAlert && <button type="button" onClick={cancel} autoFocus={danger && !isPrompt} data-type="body-s"
            className="h-9 rounded-lg border border-outline-variant/50 bg-surface-high px-4 text-on-surface-var hover:bg-surface-highest">{cancelLabel ?? 'Cancel'}</button>}
          <button type="button" onClick={submit} autoFocus={!danger && !isPrompt}
            {...unavailableWhen(isPrompt && !canSubmit, 'Fill in the required fields first')} data-type="body-s"
            className="h-9 rounded-lg px-4 transition-colors aria-disabled:cursor-not-allowed aria-disabled:opacity-40"
            style={danger ? { background: 'var(--color-danger)', color: 'var(--color-on-danger)' } : { background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
            {confirmLabel ?? (isPrompt ? 'Save' : isAlert ? 'OK' : 'Confirm')}
          </button>
        </footer>
      </motion.div>
    </motion.div>, document.body,
  )
}

function PromptField({ field, id, value, error, autoFocus, onChange }: {
  field: DialogField; id: string; value: string; error?: string; autoFocus: boolean; onChange: (value: string) => void
}) {
  const props = { id, value, autoFocus, placeholder: field.placeholder, 'aria-label': field.label ?? field.placeholder ?? field.name,
    'aria-required': field.required || undefined, 'aria-invalid': !!error, 'aria-describedby': error ? `${id}-error` : undefined }
  const chrome = `w-full rounded-lg border bg-surface-high px-3 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary ${error ? 'border-danger' : 'border-outline-variant/40'}`
  return <div>
    {field.label && <label htmlFor={id} data-type="body-s" className="mb-1 block text-on-surface-var">{field.label}</label>}
    {field.type === 'textarea'
      ? <textarea {...props} rows={4} onChange={(event) => onChange(event.target.value)} data-type="body-m" className={`${chrome} min-h-[88px] resize-y py-2`} />
      : <input {...props} type={field.type === 'password' ? 'password' : 'text'} onChange={(event) => onChange(event.target.value)} data-type="body-m" className={`${chrome} h-10`} />}
    {error && <div id={`${id}-error`} role="alert" data-type="caption" className="mt-1 text-danger">{error}</div>}
  </div>
}
