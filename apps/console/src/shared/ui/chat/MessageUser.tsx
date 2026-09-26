import { useState } from 'react'
import { fvs } from '../../theme/fontWeight'
import { motion } from 'framer-motion'
import { Sparkles, ChevronRight } from 'lucide-react'
import { messageEnter, spring } from '../../theme/motion'
import { MessageBody, type TurnPaste } from '../../../features/chat/PasteChip'
import { clockTime, fullStamp, isoStamp } from '../../data/epoch'
import './chatPresentation.css'

const travelEnter = () => ({
  initial: { opacity: 0, y: 120, scale: 0.94 },
  animate: { opacity: 1, y: 0, scale: 1, transition: spring.spatialSlow },
})

/** User turn — right-aligned contained bubble. Content renders as
 *  markdown (same renderer as assistant turns), with first/last-child margins
 *  collapsed so a one-line message sits snug. `fromComposer` makes the newest
 *  sent bubble travel up from the composer (Stage 3 glow-travel). */
export function MessageUser({ children, fromComposer = false, onFileClick, pastes, optimized, timestamp }: { children: string; fromComposer?: boolean; onFileClick?: (path: string) => void; pastes?: TurnPaste[]; optimized?: string; timestamp?: string }) {
  const time = clockTime(timestamp)
  return (
    <motion.div variants={fromComposer ? travelEnter() : messageEnter} initial="initial" animate="animate" className="flex justify-end">
      <div className="flex w-full min-w-0 flex-col items-end gap-1">
        <div
          className="gideon-chat-user text-on-surface [&_>div>*:first-child]:mt-0 [&_>div>*:last-child]:mb-0"
          data-type="body-m"
          style={fvs(400)}
        >
          <MessageBody text={children} pastes={pastes} onFileClick={onFileClick} />
          {optimized && <OptimizedDisclosure optimized={optimized} onFileClick={onFileClick} />}
        </div>
        {time && <time dateTime={isoStamp(timestamp)} title={fullStamp(timestamp)} data-type="caption" className="text-on-surface-low">{time}</time>}
      </div>
    </motion.div>
  )
}

function OptimizedDisclosure({ optimized, onFileClick }: { optimized: string; onFileClick?: (path: string) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2.5 border-t border-outline-variant/40 pt-2">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        data-type="caption"
        className="flex items-center gap-1 text-on-surface-low hover:text-on-surface-var transition-colors"
        style={fvs(500)}>
        <ChevronRight size={13} className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
        <Sparkles size={12} className="shrink-0" />
        {open ? 'Optimized prompt sent to the model' : 'Sent an optimized version'}
      </button>
      {open && (
        <div data-type="body-m" className="mt-2 rounded-lg bg-surface/60 px-3 py-2 [&_>*:first-child]:mt-0 [&_>*:last-child]:mb-0">
          <MessageBody text={optimized} onFileClick={onFileClick} />
        </div>
      )}
    </div>
  )
}
