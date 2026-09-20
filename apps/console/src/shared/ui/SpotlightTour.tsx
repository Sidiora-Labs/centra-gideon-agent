import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, ArrowRight, Check, X, type LucideIcon } from 'lucide-react'
import { Button } from './Button'
import { IconButton } from './IconButton'
import { useFocusTrap } from './useFocusTrap'
import { ease, instant, spring, useReducedMotion } from '../theme/motion'

export interface SpotlightStep {
  id: string
  anchor: string
  icon: LucideIcon
  title: string
  body: string
}

const RING_PAD = 6
const CARD_GAP = 12
const CARD_WIDTH = 320
const CARD_HEIGHT_HINT = 190
const EDGE = 12
const POLL_MS = 60
const POLL_TRIES = 40

interface Box { top: number; left: number; width: number; height: number }

export function SpotlightTour({ steps, index, label, onIndex, onExit }: {
  steps: SpotlightStep[]
  index: number
  label: string
  onIndex: (next: number) => void
  onExit: () => void
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  const step = steps[index]
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const [box, setBox] = useState<Box | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      const active = document.activeElement
      const ours = !active || active === document.body || !!trapRef.current?.contains(active)
      if (!ours) return
      e.stopPropagation()
      onExit()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onExit, trapRef])

  useEffect(() => {
    setAnchorEl(null)
    let alive = true
    let tries = 0
    let timer = 0
    const find = () => {
      if (!alive) return
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.anchor}"]`)
      if (el) { setAnchorEl(el); return }
      if (++tries > POLL_TRIES) return
      timer = window.setTimeout(find, POLL_MS)
    }
    find()
    return () => { alive = false; window.clearTimeout(timer) }
  }, [step.anchor])

  useEffect(() => {
    if (!anchorEl) { setBox(null); return }
    if (typeof anchorEl.scrollIntoView === 'function') {
      anchorEl.scrollIntoView({ block: 'center', inline: 'nearest', behavior: reduce ? 'auto' : 'smooth' })
    }
    const measure = () => {
      const r = anchorEl.getBoundingClientRect()
      const onScreen = r.width > 0 && r.height > 0
        && r.bottom > 0 && r.right > 0
        && r.top < window.innerHeight && r.left < window.innerWidth
      setBox(onScreen ? { top: r.top, left: r.left, width: r.width, height: r.height } : null)
    }
    measure()
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    ro?.observe(anchorEl)
    return () => {
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
      ro?.disconnect()
    }
  }, [anchorEl, reduce])

  useEffect(() => { trapRef.current?.focus() }, [index, anchorEl, trapRef])

  const ring = box && ringFor(box)
  const last = index === steps.length - 1
  const Icon = step.icon
  const titleId = `tour-title-${step.id}`
  const bodyId = `tour-body-${step.id}`

  return createPortal(
    <div className="fixed inset-0 z-[var(--z-modal)]">
      {
}
      <div data-tour-shield className="absolute inset-0" onClick={onExit} aria-hidden />

      {
}
      {ring ? (
        <div aria-hidden className="pointer-events-none">
          <Dim style={{ top: 0, left: 0, right: 0, height: ring.top }} reduce={reduce} />
          <Dim style={{ top: ring.top + ring.height, left: 0, right: 0, bottom: 0 }} reduce={reduce} />
          <Dim style={{ top: ring.top, left: 0, width: ring.left, height: ring.height }} reduce={reduce} />
          <Dim style={{ top: ring.top, left: ring.left + ring.width, right: 0, height: ring.height }} reduce={reduce} />
          <div className="fixed rounded-lg" style={{ ...ring, outline: '2px solid var(--color-primary)' }} />
          {
}
          {!reduce && (
            <motion.div data-tour-halo className="fixed rounded-lg"
              style={{ ...ring, outline: '2px solid var(--color-primary)' }}
              animate={{ opacity: [0.55, 0, 0.55], scale: [1, 1.05, 1] }}
              transition={{ duration: 2.4, repeat: Infinity, ease: ease.emphasized }} />
          )}
        </div>
      ) : (
        <Dim style={{ inset: 0 }} reduce={reduce} />
      )}

      <motion.div ref={trapRef} role="dialog" aria-modal="true" tabIndex={-1}
        aria-label={`${label} — step ${index + 1} of ${steps.length}: ${step.title}`}
        aria-describedby={bodyId}
        data-tour-step={step.id}
        data-tour-anchored={anchorEl ? 'true' : 'false'}
        className="squircle fixed flex flex-col gap-m bg-surface p-l shadow-sheet"
        style={cardPosition(ring)}
        initial={reduce ? false : { opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={reduce ? instant : spring.spatialFast}>
        <div className="flex items-start gap-s">
          <span className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
            <Icon size={15} className="text-primary" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <p id={titleId} data-type="title-s" className="text-on-surface">{step.title}</p>
            <p id={bodyId} data-type="body-s" className="mt-1 text-on-surface-low">{step.body}</p>
          </div>
          <IconButton icon={X} label="End the tour" onClick={onExit} size={30} iconSize={15}
            className="shrink-0 text-on-surface-low" />
        </div>
        <div className="flex items-center justify-between gap-s">
          <span data-type="caption" className="text-on-surface-low tabular-nums">
            Step {index + 1} of {steps.length}
          </span>
          <div className="flex shrink-0 items-center gap-1.5">
            {index > 0 && (
              <Button variant="ghost" size="sm" onClick={() => onIndex(index - 1)}>
                <ArrowLeft size={15} /> Back
              </Button>
            )}
            <Button variant="primary" size="sm" onClick={last ? onExit : () => onIndex(index + 1)}>
              {last ? <>Done <Check size={15} /></> : <>Next <ArrowRight size={15} /></>}
            </Button>
          </div>
        </div>
      </motion.div>
    </div>,
    document.body,
  )
}

function ringFor(box: Box): Box {
  const top = Math.max(0, box.top - RING_PAD)
  const left = Math.max(0, box.left - RING_PAD)
  return {
    top,
    left,
    width: Math.min(box.width + RING_PAD * 2, window.innerWidth - left),
    height: Math.min(box.height + RING_PAD * 2, window.innerHeight - top),
  }
}

function Dim({ style, reduce }: { style: React.CSSProperties; reduce: boolean | null }) {
  return (
    <motion.div aria-hidden className="pointer-events-none fixed bg-canvas/70" style={style}
      initial={reduce ? false : { opacity: 0 }} animate={{ opacity: 1 }}
      transition={reduce ? instant : spring.effects} />
  )
}

function cardPosition(ring: Box | null): React.CSSProperties {
  const centred: React.CSSProperties = {
    top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: CARD_WIDTH,
  }
  if (!ring) return centred
  const vw = window.innerWidth
  const vh = window.innerHeight
  const clamp = (v: number, max: number) => Math.min(Math.max(EDGE, v), Math.max(EDGE, max))
  const centredOn = (v: number, span: number, extent: number, limit: number) =>
    clamp(v + span / 2 - extent / 2, limit - extent - EDGE)

  const below = ring.top + ring.height + CARD_GAP
  if (below + CARD_HEIGHT_HINT < vh - EDGE) {
    return { top: below, left: centredOn(ring.left, ring.width, CARD_WIDTH, vw), width: CARD_WIDTH }
  }
  const above = ring.top - CARD_GAP - CARD_HEIGHT_HINT
  if (above > EDGE) {
    return { top: above, left: centredOn(ring.left, ring.width, CARD_WIDTH, vw), width: CARD_WIDTH }
  }
  const y = centredOn(ring.top, ring.height, CARD_HEIGHT_HINT, vh)
  const right = ring.left + ring.width + CARD_GAP
  if (right + CARD_WIDTH < vw - EDGE) return { top: y, left: right, width: CARD_WIDTH }
  const left = ring.left - CARD_GAP - CARD_WIDTH
  if (left > EDGE) return { top: y, left, width: CARD_WIDTH }
  return centred
}
