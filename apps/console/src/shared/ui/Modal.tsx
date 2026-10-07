import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { X } from 'lucide-react'
import { IconButton } from './IconButton'
import { useFocusTrap } from './useFocusTrap'
import { useDismissKey } from './overlayInteraction'
import { spring, physics, expr, useReducedMotion } from '../theme/motion'
import { cx } from './cx'

type Presentation = 'centered' | 'drawer' | 'bottom-sheet' | 'fullscreen'
let scrollLocks = 0
let previousOverflow = ''

export function Modal({ title, icon, onClose, children, layoutId, presentation = 'centered',
  open = true, keepMounted = false, initialFocus, restoreFocus, dismissOnBackdrop = true, lockBodyScroll = false,
  chrome = 'standard', closeLabel = 'Close', headerActions, mediaControls }: {
  title: ReactNode
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
  layoutId?: string
  presentation?: Presentation
  open?: boolean
  keepMounted?: boolean
  initialFocus?: RefObject<HTMLElement | null>
  restoreFocus?: RefObject<HTMLElement | null>
  dismissOnBackdrop?: boolean
  lockBodyScroll?: boolean
  chrome?: 'standard' | 'media'
  closeLabel?: string
  headerActions?: ReactNode
  mediaControls?: ReactNode
}) {
  const titleId = useId()
  const media = chrome === 'media' && presentation === 'fullscreen'
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const trapRef = useFocusTrap<HTMLDivElement>({ enabled: open, initialFocus: initialFocus ?? (media ? closeRef : undefined), restoreFocus })
  const reduced = useReducedMotion()
  useDismissKey('Escape', onClose, 100, open)
  useEffect(() => {
    if (!open || !lockBodyScroll) return
    if (scrollLocks++ === 0) previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      if (--scrollLocks === 0) document.body.style.overflow = previousOverflow
    }
  }, [open, lockBodyScroll])
  if (!open && !keepMounted) return null
  const resting = { opacity: 1, scale: 1, y: 0 }
  const hidden = { opacity: 0, scale: reduced ? 1 : 1 - expr(0.025, 0.5), y: reduced ? 0 : expr(12, 0.4) }

  const sheet = (
    <motion.div hidden={!open} inert={!open || undefined} aria-hidden={!open || undefined} style={!open ? { display: 'none' } : undefined}
      className={cx('fixed inset-0 z-[var(--z-modal)] flex min-h-0 overflow-y-auto overscroll-contain',
        presentation === 'centered' && 'items-center justify-center p-s sm:p-2xl',
        presentation === 'drawer' && 'items-stretch justify-end',
        presentation === 'bottom-sheet' && 'items-end justify-center p-l',
        presentation === 'fullscreen' && 'items-stretch justify-center')}
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <motion.div className={cx('absolute inset-0', media ? 'bg-black/80' : 'bg-canvas/70 backdrop-blur-sm')} onClick={dismissOnBackdrop && open ? onClose : undefined} aria-hidden="true" />
      <motion.div ref={trapRef} role="dialog" aria-modal="true" aria-labelledby={titleId} layoutId={layoutId}
        className={cx('relative flex min-h-0 min-w-0 w-full max-w-full flex-col overflow-hidden',
          !media && 'border border-outline-variant/50 bg-surface shadow-sheet',
          presentation === 'centered' && 'max-h-[calc(100dvh-1rem)] rounded-2xl',
          presentation === 'drawer' && 'h-full max-h-[100dvh] rounded-l-2xl',
          presentation === 'bottom-sheet' && 'max-h-[80dvh] rounded-2xl',
          presentation === 'fullscreen' && 'h-full max-h-[100dvh]')}
        style={presentation === 'centered' ? { maxWidth: 'var(--modal-width)' }
          : presentation === 'drawer' ? { width: '18rem', maxWidth: '85vw' }
          : presentation === 'bottom-sheet' ? { maxWidth: '40rem' } : undefined}
        initial={hidden} animate={resting} exit={hidden} transition={reduced ? spring.effects : physics.fluid}>
        {media ? <h2 id={titleId} className="sr-only">{title}</h2> : <header className="flex min-w-0 shrink-0 items-start gap-m border-b border-outline-variant/40 bg-surface-high/40 px-s py-m sm:px-l">
          {icon && <span className="shrink-0 text-primary">{icon}</span>}
          <h2 id={titleId} data-type="title-l" className="min-w-0 max-h-[35dvh] flex-1 overflow-y-auto break-words text-on-surface">{title}</h2>
          {headerActions}
          <IconButton icon={X} label={closeLabel} size={34} onClick={onClose} />
        </header>}
        <div className={media ? 'relative min-h-0 min-w-0 flex-1 overflow-hidden' : 'min-h-0 min-w-0 flex-1 overflow-x-auto overflow-y-auto overscroll-contain break-words px-s py-m sm:px-l sm:py-l'}>{children}</div>
        {media && <div data-slot="modal-media-toolbar" className="absolute end-l top-l flex items-center gap-xs rounded-l border border-outline-variant/50 bg-surface/80 p-xs">
          {mediaControls}
          <span ref={(node) => { closeRef.current = node?.querySelector('button') ?? null }}>
            <IconButton icon={X} label={closeLabel} size={34} onClick={onClose} />
          </span>
        </div>}
      </motion.div>
    </motion.div>
  )
  return createPortal(sheet, document.body)
}
