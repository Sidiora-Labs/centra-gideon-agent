import { useId, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { X } from 'lucide-react'
import { IconButton } from './IconButton'
import { useFocusTrap } from './useFocusTrap'
import { useDismissKey } from './overlayInteraction'
import { spring, physics, expr, useReducedMotion } from '../theme/motion'

export function Modal({ title, icon, onClose, children, layoutId }: {
  title: ReactNode
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
  layoutId?: string
}) {
  const titleId = useId()
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduced = useReducedMotion()
  useDismissKey('Escape', onClose, 100)
  const resting = { opacity: 1, scale: 1, y: 0 }
  const hidden = { opacity: 0, scale: reduced ? 1 : 1 - expr(0.025, 0.5), y: reduced ? 0 : expr(12, 0.4) }

  const sheet = (
    <motion.div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-l sm:p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <motion.div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <motion.div ref={trapRef} role="dialog" aria-modal="true" aria-labelledby={titleId} layoutId={layoutId}
        className="relative flex max-h-full w-full flex-col overflow-hidden rounded-2xl border border-outline-variant/50 bg-surface shadow-sheet"
        style={{ maxWidth: 'var(--modal-width)' }}
        initial={hidden} animate={resting} exit={hidden} transition={reduced ? spring.effects : physics.fluid}>
        <header className="flex shrink-0 items-start gap-m border-b border-outline-variant/40 bg-surface-high/40 px-l py-m">
          {icon && <span className="shrink-0 text-primary">{icon}</span>}
          <h2 id={titleId} data-type="title-l" className="min-w-0 max-h-[35dvh] flex-1 overflow-y-auto break-words text-on-surface">{title}</h2>
          <IconButton icon={X} label="Close" size={34} onClick={onClose} />
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">{children}</div>
      </motion.div>
    </motion.div>
  )
  return createPortal(sheet, document.body)
}
