import { useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { PanelLeftClose, PanelLeftOpen, SquareTerminal } from 'lucide-react'
import { spring } from '../theme/motion'
import { ThemeControl } from './TopBar'
import { WidthPill } from './WidthPill'
import { NotificationBell } from './NotificationBell'
import { SystemWidget } from './SystemWidget'
import { DegradedChip } from './DegradedChip'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { accentChip } from '../theme/accent'


export function ShellCornerLeft({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-l')
  return (
    <div ref={ref} className="gideon-corner-left absolute left-0 top-0 z-30 py-2">
      <button type="button" onClick={onToggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        className="grid h-9 w-8 place-items-center rounded-r-lg border border-l-0 border-outline-variant/40 bg-surface-low/80 text-on-surface-low backdrop-blur-sm transition-colors hover:bg-surface-low hover:text-on-surface">
        {
}
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={collapsed ? 'open' : 'close'} initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }} transition={spring.spatialFast} className="grid place-items-center">
            {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </motion.span>
        </AnimatePresence>
      </button>
    </div>
  )
}

export function ShellCornerRight({ terminalOpen, onToggleTerminal, navigate }: { terminalOpen: boolean; onToggleTerminal: () => void; navigate: (path: string) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useShellCornerWidth(ref, '--shell-corner-r')
  useShellCornerHeight(ref, '--shell-corner-rh')
  const isMobile = useIsMobile()
  return (
    <div ref={ref} className="gideon-corner-right absolute right-0 top-0 z-30 flex items-center">
      <div className="flex items-center gap-1 rounded-bl-xl border border-r-0 border-t-0 border-outline-variant/40 bg-surface-low/80 px-1.5 py-1.5 backdrop-blur-sm">
        <button type="button" onClick={onToggleTerminal}
          aria-label={terminalOpen ? 'Hide terminal' : 'Open terminal'} title={terminalOpen ? 'Hide terminal (⌘`)' : 'Open terminal (⌘`)'}
          className="grid size-7 place-items-center rounded-pill transition-colors"
          style={terminalOpen
            ? accentChip
            : { color: 'var(--color-on-surface-low)' }}>
          <SquareTerminal size={16} />
        </button>
        {!isMobile && (
          <>
            <span className="h-4 w-px bg-outline-variant/40" aria-hidden />
            <WidthPill />
          </>
        )}
        <NotificationBell navigate={navigate} />
        <ThemeControl />
        {
}
        <DegradedChip />
        { }
        <span className="h-4 w-px bg-outline-variant/40" aria-hidden />
        <SystemWidget />
      </div>
    </div>
  )
}

function useShellCornerWidth(ref: React.RefObject<HTMLDivElement | null>, varName: string) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => document.documentElement.style.setProperty(varName, `${Math.ceil(el.getBoundingClientRect().width)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, varName])
}

function useShellCornerHeight(ref: React.RefObject<HTMLDivElement | null>, varName: string) {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const set = () => document.documentElement.style.setProperty(varName, `${Math.ceil(el.getBoundingClientRect().height)}px`)
    set()
    const ro = new ResizeObserver(set)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref, varName])
}
