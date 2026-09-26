import { useCallback, useId, useReducer, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { X, Maximize2, Minimize2 } from 'lucide-react'
import { useFocusReturn } from './useFocusReturn'
import { useResizablePanel } from './useResizablePanel'
import { useDismissKey, useDockReservation } from './overlayInteraction'
import { IconButton } from './IconButton'
import { spring, physics, expr, useReducedMotion } from '../theme/motion'
import type { RouteProps } from '../../app/shell/useQueryState'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { MobileSheet } from './MobileSheet'

const DOCK_MIN = 320
const DOCK_MAX = 720
const dockMaxWidth = () => Math.min(DOCK_MAX, Math.max(DOCK_MIN, Math.floor(window.innerWidth / 2)))

export function SidePanel({ title, icon, onClose, urlKey, storeKey = 'sidepanel-w', fillHeight = false, onExpand, children }: {
  title: ReactNode
  icon?: ReactNode
  onClose: () => void
  urlKey?: { key: string; setQuery: RouteProps['setQuery'] }
  storeKey?: string
  fillHeight?: boolean
  onExpand?: () => void
  children: ReactNode
}) {
  const rtl = document.documentElement.dir === 'rtl'
  const [bodyHost] = useState(() => document.createElement('div'))
  const attachBody = useCallback((node: HTMLDivElement | null) => {
    if (node && bodyHost.parentNode !== node) node.appendChild(bodyHost)
  }, [bodyHost])
  const titleId = useId()
  const mobile = useIsMobile()
  const reduced = useReducedMotion()
  const focusReturnRef = useFocusReturn<HTMLDivElement>()
  const [mode, setMode] = useReducer((_current: 'dock' | 'full', next: 'dock' | 'full') => next, 'dock')
  const expanded = mode === 'full'
  const { fitWidth: dockW, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    storeKey.replace(/-w$/, ''), { def: Math.min(420, dockMaxWidth()), min: DOCK_MIN, max: dockMaxWidth, side: rtl ? 'left' : 'right', edgePeek: Math.ceil(window.innerWidth / 2), storageKey: storeKey })
  const close = () => {
    urlKey?.setQuery({ [urlKey.key]: null })
    onClose()
  }
  const changeMode = () => {
    if (expanded) setMode('dock')
    else if (onExpand) onExpand()
    else setMode('full')
  }
  useDockReservation(!expanded && !mobile)
  useDismissKey('Escape', () => expanded ? setMode('dock') : close(), expanded ? 20 : 10)

  if (mobile) return <><MobileSheet title={title} icon={icon} fullHeight onClose={close}
    actions={onExpand && <IconButton icon={Maximize2} label="Open full page" size={44} onClick={onExpand} />}>
    <div ref={attachBody} />
  </MobileSheet>{createPortal(children, bodyHost)}</>

  const header = (
    <header className="flex shrink-0 items-center gap-s border-b border-outline-variant/40 bg-surface-high/40 px-l py-m">
      <div className="flex min-w-0 flex-1 items-center gap-s">{icon}
        <h2 id={titleId} data-type="title-l" className="text-on-surface truncate" title={typeof title === 'string' ? title : undefined}>{title}</h2>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <IconButton icon={expanded ? Minimize2 : Maximize2} size={34} onClick={changeMode}
          label={expanded ? 'Collapse to panel' : onExpand ? 'Open full page' : 'Expand to full width'} />
        <IconButton icon={X} label="Close" size={34} onClick={close} />
      </div>
    </header>
  )
  const body = <div ref={attachBody} className="min-h-0 flex-1 overflow-y-auto px-l py-l" />
  if (expanded) {
    const edge = rtl ? `inset(0px calc(100dvw - ${dockW}px) 0px 0px)` : `inset(0px 0px 0px calc(100dvw - ${dockW}px))`
    return <>{createPortal(
      <motion.div ref={focusReturnRef} role="region" aria-labelledby={titleId}
        className="fixed inset-0 z-[var(--z-content)] flex flex-col bg-surface"
        initial={reduced ? { opacity: 0 } : { clipPath: edge }}
        animate={reduced ? { opacity: 1 } : { clipPath: 'inset(0px 0px 0px 0px)' }}
        exit={reduced ? { opacity: 0 } : { clipPath: edge }} transition={reduced ? spring.effects : physics.fluid}>
        {header}
        <div className="mx-auto flex min-h-0 w-full flex-1 flex-col" style={{ maxWidth: 'var(--side-panel-width)' }}>{body}</div>
      </motion.div>, document.body,
    )}{createPortal(children, bodyHost)}</>
  }
  const top = fillHeight ? '0px' : 'var(--shell-corner-rh, 56px)'
  const bottom = 'var(--spacing-m, 12px)'
  return (
    <><motion.div ref={focusReturnRef} role="region" aria-labelledby={titleId}
      className="relative shrink-0 overflow-hidden rounded-s-2xl border-s border-outline-variant/50 bg-surface shadow-sm"
      style={{ marginTop: top, marginBottom: bottom, height: `calc(100% - ${top} - ${bottom})` }}
      initial={{ width: reduced ? dockW : 0, opacity: 0 }} animate={{ width: dockW, opacity: 1 }}
      exit={{ width: reduced ? dockW : 0, opacity: 0 }} transition={reduced ? spring.effects : spring.spatialDefault}>
      <div role="separator" aria-orientation="vertical" tabIndex={0} aria-label="Resize panel — arrow keys to resize"
        aria-valuenow={Math.round(dockW)} aria-valuemin={min} aria-valuemax={max}
        onPointerDown={onHandleDown} onKeyDown={onHandleKey}
        className="group absolute inset-y-0 start-0 z-20 w-1.5 cursor-ew-resize outline-none focus-visible:bg-primary/20">
        <motion.span className="absolute inset-y-0 start-0 bg-outline-variant/50 group-hover:bg-primary group-focus-visible:bg-primary"
          initial={false} animate={{ width: 1 }} whileHover={{ width: reduced ? 1 : 1 + expr(2.5, 0.3) }} transition={physics.snappy} />
      </div>
      <div className="flex h-full flex-col" style={{ width: dockW }}>{header}{body}</div>
    </motion.div>{createPortal(children, bodyHost)}</>
  )
}
