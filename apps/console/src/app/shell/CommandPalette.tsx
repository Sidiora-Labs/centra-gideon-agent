import { useCallback, useEffect, useId, useMemo, useReducer, useRef } from 'react'
import { ArrowRight, CornerDownLeft, type LucideIcon } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { SearchField } from '../../shared/ui/SearchField'
import { initialPalette, paletteReducer, searchCommands } from './paletteState'

export interface Command { id: string; label: string; hint?: string; icon: LucideIcon; keywords?: string; run: () => void }
export function CommandPalette({ commands, open, onOpenChange }: { commands: Command[]; open?: boolean; onOpenChange?: (open: boolean) => void }) {
  const [state, dispatch] = useReducer(paletteReducer, initialPalette)
  const close = useCallback(() => { dispatch({ type: 'close' }); onOpenChange?.(false) }, [onOpenChange])
  useEffect(() => { if (open) dispatch({ type: 'open' }) }, [open])
  useEffect(() => {
    const toggle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        if (open === undefined) dispatch({ type: 'toggle' })
        else onOpenChange?.(!open)
      }
    }
    window.addEventListener('keydown', toggle)
    return () => window.removeEventListener('keydown', toggle)
  }, [open, onOpenChange])
  const results = useMemo(() => searchCommands(commands, state.query), [commands, state.query])
  const run = (command?: Command) => { if (command) { close(); command.run() } }
  return (open ?? state.open) ? <Modal title="Command palette" onClose={close}>
    <PaletteResults query={state.query} results={results} cursor={Math.min(state.cursor, Math.max(0, results.length - 1))}
      search={(value) => dispatch({ type: 'search', value })} select={(index) => dispatch({ type: 'select', index })}
      move={(delta) => dispatch({ type: 'move', delta, count: results.length })} run={run} />
  </Modal> : null
}
function PaletteResults({ query, results, cursor, search, select, move, run }: {
  query: string; results: Command[]; cursor: number; search: (value: string) => void
  select: (index: number) => void; move: (delta: number) => void; run: (command?: Command) => void
}) {
  const id = useId(), input = useRef<HTMLInputElement>(null), list = useRef<HTMLDivElement>(null)
  useEffect(() => { input.current?.focus() }, [])
  useEffect(() => { list.current?.querySelector<HTMLElement>(`[data-cmd-idx="${cursor}"]`)?.scrollIntoView?.({ block: 'nearest' }) }, [cursor])
  return <div className="mx-auto flex w-full max-w-3xl flex-col gap-m">
    <div className="rounded-xl border border-outline-variant bg-surface-high px-m py-s">
      <SearchField variant="inline" inlineIconSize={18} clearable={false} inputRef={input} value={query} onChange={search}
        placeholder="Search pages and actions…" ariaLabel="Search pages and actions" ariaHasPopup="listbox" ariaControls={`${id}-commands`}
        ariaActiveDescendant={results.length ? `${id}-command-${cursor}` : undefined}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); move(event.key === 'ArrowDown' ? 1 : -1) }
          if (event.key === 'Enter') { event.preventDefault(); run(results[cursor]) }
        }} />
    </div>
    <div ref={list} role="listbox" aria-label="Commands" id={`${id}-commands`} className="max-h-[50vh] overflow-y-auto rounded-xl border border-outline-variant/40 p-1">
      {results.map((command, index) => <button key={command.id} type="button" role="option" aria-selected={index === cursor}
        id={`${id}-command-${index}`} data-cmd-idx={index} onMouseEnter={() => select(index)} onClick={() => run(command)}
        className="flex min-h-11 w-full items-center gap-m rounded-lg px-m py-s text-left focus-visible:-outline-offset-2"
        style={{ background: index === cursor ? 'var(--color-surface-high)' : undefined }}>
        <command.icon size={17} className="shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate text-on-surface">{command.label}</span>
        {command.hint && <span className="text-on-surface-low text-[0.75rem]">{command.hint}</span>}
        {index === cursor && <CornerDownLeft size={14} className="text-on-surface-low" />}
      </button>)}
      {!results.length && <p className="p-l text-center text-on-surface-low">No matches for “{query}”.</p>}
    </div>
    <div className="flex items-center gap-m text-on-surface-low text-[0.75rem]">
      <span className="inline-flex items-center gap-xs"><ArrowRight size={12} className="rotate-90" /> navigate</span>
      <span className="inline-flex items-center gap-xs"><CornerDownLeft size={12} /> select</span>
      <kbd className="ml-auto font-mono">⌘K / Ctrl+K</kbd>
    </div>
  </div>
}
