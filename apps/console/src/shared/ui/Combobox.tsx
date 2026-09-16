import { useEffect, useId, useMemo, useReducer, useRef, type KeyboardEvent } from 'react'
import { motion } from 'framer-motion'
import { ChevronDown, Search, Check, X } from 'lucide-react'
import { spring } from '../theme/motion'
import { useFieldHintId } from './forms'
import { choiceProjection, choiceReducer, closedChoice, type Choice } from './interactionState'

export interface ComboOption extends Choice {}

export function Combobox({ options, value, onChange, placeholder = 'Select…', emptyText = 'No matches' }: {
  options: ComboOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  emptyText?: string
}) {
  const hintId = useFieldHintId()
  const identity = useId()
  const listId = `combo-list-${identity}`
  const [state, dispatch] = useReducer(choiceReducer, closedChoice)
  const root = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLInputElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const { groups, ordered } = useMemo(() => choiceProjection(options, state.query), [options, state.query])
  const cursor = Math.max(0, Math.min(state.cursor, ordered.length - 1))
  const rowId = `${listId}-opt-${cursor}`
  const selected = options.find((option) => option.value === value)

  useEffect(() => {
    if (state.open) field.current?.focus()
    else if (state.restoreFocus) trigger.current?.focus()
  }, [state.open, state.restoreFocus])
  useEffect(() => {
    if (state.open) document.getElementById(rowId)?.scrollIntoView?.({ block: 'nearest' })
  }, [rowId, state.open, ordered])
  useEffect(() => {
    if (!state.open) return
    const outside = (event: MouseEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) dispatch({ type: 'close' })
    }
    document.addEventListener('mousedown', outside)
    return () => document.removeEventListener('mousedown', outside)
  }, [state.open])

  const commit = (next: string) => {
    dispatch({ type: 'close', restore: true })
    onChange(next)
  }
  const navigate = (event: KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault()
        dispatch({ type: 'cursor', index: cursor + (event.key === 'ArrowDown' ? 1 : -1), count: ordered.length })
        break
      case 'Enter':
        event.preventDefault()
        if (ordered[cursor]) commit(ordered[cursor].value)
        break
      case 'Escape':
        event.preventDefault()
        event.stopPropagation()
        dispatch({ type: 'close', restore: true })
        break
    }
  }

  return (
    <div ref={root} className="relative" onBlurCapture={(event) => {
      if (!event.relatedTarget || !event.currentTarget.contains(event.relatedTarget)) dispatch({ type: 'close' })
    }}>
      <motion.div layout transition={spring.spatialDefault}
        className={`overflow-hidden rounded-lg border ${state.open ? 'border-primary/40 bg-surface-high shadow-menu' : 'border-outline-variant/50 bg-surface-container'}`}>
        {state.open ? <>
          <div className="relative border-b border-outline-variant/30 p-2">
            <Search size={14} aria-hidden className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-low" />
            <input ref={field} value={state.query} onChange={(event) => dispatch({ type: 'query', value: event.target.value })}
              onKeyDown={navigate} role="combobox" aria-expanded aria-controls={listId} aria-autocomplete="list"
              aria-label="Search options" aria-activedescendant={ordered[cursor] ? rowId : undefined}
              placeholder="Search…" data-type="body-s"
              className="h-8 w-full rounded-md bg-surface pl-8 pr-2 text-on-surface outline-none placeholder:text-on-surface-low focus:ring-2 focus:ring-inset focus:ring-primary" />
          </div>
          <div id={listId} role={ordered.length ? 'listbox' : undefined} tabIndex={-1} className="max-h-64 overflow-y-auto p-1">
            {ordered.length === 0 && <div role="status" data-type="body-s" className="px-3 py-3 text-on-surface-low">{emptyText}</div>}
            {groups.map(({ label, rows }) => (
              <div key={label} role={label ? 'group' : undefined} aria-label={label || undefined}>
                {label && <div data-type="caption" className="px-3 pb-1 pt-2 font-medium text-on-surface-low">{label}</div>}
                {rows.map(({ choice, index }) => (
                  <button key={choice.value} id={`${listId}-opt-${index}`} type="button" role="option" tabIndex={-1}
                    aria-selected={choice.value === value} onClick={() => commit(choice.value)}
                    onMouseMove={() => dispatch({ type: 'cursor', index, count: ordered.length })}
                    className="relative flex w-full items-center gap-s rounded-md px-3 py-2 text-left">
                    {index === cursor && <motion.span layoutId={`combo-active-${identity}`} transition={spring.spatialFast}
                      className="absolute inset-0 rounded-md bg-primary/15" />}
                    <span className="relative min-w-0 flex-1">
                      <span data-type="body-s" className="block truncate text-on-surface">{choice.label}</span>
                      {choice.description && <span data-type="caption" className="block truncate text-on-surface-low">{choice.description}</span>}
                    </span>
                    {choice.value === value && <Check size={14} aria-hidden className="relative shrink-0 text-primary" />}
                  </button>
                ))}
              </div>
            ))}
          </div>
        </> : <button ref={trigger} type="button" onClick={() => dispatch({ type: 'open' })} data-type="title-m"
          aria-haspopup="listbox" aria-expanded={false} aria-describedby={hintId}
          className="flex h-10 w-full items-center gap-s px-m text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary">
          <span className={`min-w-0 flex-1 truncate ${value !== '' ? 'pr-6' : ''} ${selected ? 'text-on-surface' : 'text-on-surface-low'}`}>{selected?.label ?? placeholder}</span>
          <ChevronDown size={16} aria-hidden className="shrink-0 text-on-surface-low" />
        </button>}
      </motion.div>
      {!state.open && value !== '' && <button type="button" aria-label="Clear selection" title="Clear selection" onClick={() => commit('')}
        className="absolute right-8 top-5 grid size-6 -translate-y-1/2 place-items-center rounded-md text-on-surface-low hover:bg-surface-highest hover:text-on-surface focus-visible:ring-2 focus-visible:ring-primary">
        <X size={14} aria-hidden />
      </button>}
    </div>
  )
}
