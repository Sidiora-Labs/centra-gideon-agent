import { useCallback, useEffect, useState } from 'react'
import { notify } from '../../app/shell/appSdk'
import { fvs } from '../../shared/theme/fontWeight'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Terminal as TermIcon, Plus, X, ChevronDown, Maximize2, Loader2 } from 'lucide-react'
import { spring } from '../../shared/theme/motion'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { api } from '../../shared/data/api'
import { TerminalView } from './TerminalView'
import type { TermTab } from './TerminalPage'
import { tabListKeys } from '../../shared/data/tabListKeys'
import { useResizablePanel } from '../../shared/ui/useResizablePanel'

const HEIGHT_KEY = 'terminal-drawer-h'
const MIN_H = 160, MAX_FRAC = 0.85, DEF_H = 320

export function TerminalDrawer({ open, onClose, onOpenFull }: {
  open: boolean
  onClose: () => void
  onOpenFull: () => void
}) {
  const [tabs, setTabs] = useState<TermTab[]>([])
  const [active, setActive] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { width: height, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    'terminal-drawer', { storageKey: HEIGHT_KEY, def: DEF_H, min: MIN_H, max: () => window.innerHeight * MAX_FRAC, side: 'bottom' })

  useEffect(() => {
    if (open && tabs.length === 0 && !busy) void newSession()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const newSession = useCallback(async () => {
    setBusy(true); setError('')
    try {
      const r = await api.createTerminal()
      setTabs((t) => [...t, { id: r.session_id, label: `Session ${t.length + 1}`, cwd: r.cwd, shell: r.shell }])
      setActive(r.session_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open a terminal session.')
    } finally { setBusy(false) }
  }, [])

  const closeSession = useCallback(async (id: string) => {
    try {
      await api.deleteTerminal(id)
    } catch (e) {
      notify(`Couldn't close the terminal session: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setTabs((t) => {
      const next = t.filter((x) => x.id !== id)
      setActive((cur) => (cur === id ? (next.length ? next[next.length - 1].id : '') : cur))
      return next
    })
  }, [])

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-x-0 bottom-0 z-40 flex flex-col border-t border-outline-variant/50 bg-surface/97 shadow-2xl backdrop-blur-md"
          style={{ height }}
          initial={{ y: '100%' }} animate={{ y: 0 }} exit={{ y: '100%' }} transition={spring.spatialDefault}>
          {
}
          <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="horizontal"
            tabIndex={0} aria-label="Resize terminal drawer — arrow keys to resize"
            aria-valuenow={Math.round(height)} aria-valuemin={min} aria-valuemax={Math.round(max)}
            className="group absolute inset-x-0 -top-1 z-10 h-2 cursor-ns-resize outline-none">
            <span className="absolute inset-x-0 top-1 h-px bg-transparent transition-colors group-hover:bg-primary/60 group-focus-visible:bg-primary" />
          </div>

          { }
          <div className="flex items-center gap-1 border-b border-outline-variant/40 px-2 py-1.5">
            <TermIcon size={13} className="ml-1 shrink-0 text-on-surface-low" />
            <div className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
              {
}
              <div role="tablist" aria-label="Terminal sessions" onKeyDown={tabListKeys((i) => setActive(tabs[i].id))}
                className="flex items-stretch gap-1">
              {tabs.map((t) => {
                const on = t.id === active
                return (
                  <div key={t.id} role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
                    onClick={() => setActive(t.id)}
                    aria-keyshortcuts="Delete"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActive(t.id); return }
                      if (e.key === 'Delete') { e.preventDefault(); closeSession(t.id) }
                    }}
                    className="group inline-flex h-7 shrink-0 cursor-pointer items-center gap-1 rounded-md pl-2.5 pr-1 text-[0.75rem] transition-colors"
                    style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    <span className="mr-0.5">{t.label}</span>
                    { }
                    <button type="button" onClick={(e) => { e.stopPropagation(); closeSession(t.id) }}
                      aria-label={`Close ${t.label}`} title="Close session"
                      className="grid size-6 -mr-0.5 shrink-0 place-items-center rounded opacity-50 hover:bg-surface-highest hover:opacity-100"><X size={11} /></button>
                  </div>
                )
              })}
              </div>
              <SquareIconButton label="New session" onClick={() => newSession()} className="shrink-0">
                {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={14} />}
              </SquareIconButton>
            </div>
            <SquareIconButton icon={Maximize2} iconSize={13} label="Open full terminal" onClick={onOpenFull} className="shrink-0" />
            <SquareIconButton icon={ChevronDown} iconSize={15} label="Hide terminal (⌘`)" onClick={onClose} className="shrink-0" />
          </div>

          { }
          <div className="relative min-h-0 flex-1">
            {tabs.length === 0 ? (
              error ? (
                <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
                  <div className="text-on-surface text-[0.8125rem]" style={fvs(500)}>Couldn’t open a session</div>
                  <div className="max-w-md text-on-surface-low text-[0.8125rem]">{error}</div>
                  <button type="button" onClick={() => newSession()} disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-pill px-4 h-9 text-[0.8125rem] disabled:opacity-50"
                    style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
                    {busy ? <Loader2 size={14} className="animate-spin" /> : null} Retry
                  </button>
                </div>
              ) : (
                <div className="flex h-full items-center justify-center text-on-surface-low text-[0.8125rem]">Opening a session…</div>
              )
            ) : tabs.map((t) => (
              <div key={t.id} className="absolute inset-0 p-1.5" style={{ display: t.id === active ? 'block' : 'none' }}>
                <TerminalView tab={t} onExited={() => {}} onClose={() => closeSession(t.id)} />
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
