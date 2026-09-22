import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { Crop } from 'lucide-react'
import { Button } from './Button'
import { useFocusTrap } from './useFocusTrap'
import { spring, physics, expr, useReducedMotion } from '../theme/motion'
import { cropViewStyle, type SnipRect } from './composer/displayCapture'

const STEP = 24
const FAST = 8
const MIN_SIDE = 16

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

function contain(rect: SnipRect, width: number, height: number): SnipRect {
  const w = clamp(rect.width, MIN_SIDE, width)
  const h = clamp(rect.height, MIN_SIDE, height)
  return { x: clamp(rect.x, 0, width - w), y: clamp(rect.y, 0, height - h), width: w, height: h }
}

function fromCorners(ax: number, ay: number, bx: number, by: number): SnipRect {
  return {
    x: Math.min(ax, bx),
    y: Math.min(ay, by),
    width: Math.abs(bx - ax),
    height: Math.abs(by - ay),
  }
}

export function SnipOverlay({ frame, width, height, onCancel, onConfirm }: {
  frame: string
  width: number
  height: number
  onCancel: () => void
  onConfirm: (rect: SnipRect) => void
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  const stageRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{ x: number; y: number } | null>(null)
  const [rect, setRect] = useState<SnipRect>(() => contain({ x: 0, y: 0, width, height }, width, height))

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      e.preventDefault()
      onCancel()
      return
    }
    if (e.key === 'Enter' && (e.target as HTMLElement)?.dataset?.snipStage === 'true') {
      e.preventDefault()
      onConfirm(rect)
      return
    }
    const step = e.shiftKey ? STEP * FAST : STEP
    let next: SnipRect | null = null
    if (e.key === 'ArrowLeft') next = e.altKey ? { ...rect, width: rect.width - step } : { ...rect, x: rect.x - step }
    else if (e.key === 'ArrowRight') next = e.altKey ? { ...rect, width: rect.width + step } : { ...rect, x: rect.x + step }
    else if (e.key === 'ArrowUp') next = e.altKey ? { ...rect, height: rect.height - step } : { ...rect, y: rect.y - step }
    else if (e.key === 'ArrowDown') next = e.altKey ? { ...rect, height: rect.height + step } : { ...rect, y: rect.y + step }
    if (!next) return
    e.preventDefault()
    e.stopPropagation()
    setRect(contain(next, width, height))
  }, [height, onCancel, onConfirm, rect, width])

  useEffect(() => {
    const toSource = (e: PointerEvent): { x: number; y: number } => {
      const box = stageRef.current?.getBoundingClientRect()
      if (!box || !box.width || !box.height) return { x: 0, y: 0 }
      return {
        x: clamp(((e.clientX - box.left) / box.width) * width, 0, width),
        y: clamp(((e.clientY - box.top) / box.height) * height, 0, height),
      }
    }
    const move = (e: PointerEvent) => {
      const anchor = dragRef.current
      if (!anchor) return
      const p = toSource(e)
      setRect(contain(fromCorners(anchor.x, anchor.y, p.x, p.y), width, height))
    }
    const up = () => { dragRef.current = null }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [height, width])

  const beginDrag = (e: React.PointerEvent) => {
    const box = stageRef.current?.getBoundingClientRect()
    if (!box || !box.width || !box.height) return
    const x = clamp(((e.clientX - box.left) / box.width) * width, 0, width)
    const y = clamp(((e.clientY - box.top) / box.height) * height, 0, height)
    dragRef.current = { x, y }
    setRect(contain({ x, y, width: MIN_SIDE, height: MIN_SIDE }, width, height))
  }

  const view = cropViewStyle(rect, width, height)
  const enterScale = reduce ? 1 : 1 - expr(0.04, 0.5)
  const enterY = reduce ? 0 : expr(10, 0.4)

  return createPortal(
    <motion.div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <motion.div className="absolute inset-0 bg-canvas/80 backdrop-blur-sm" onClick={onCancel}
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects} />
      <motion.div ref={trapRef} role="dialog" aria-modal="true" aria-label="Crop the captured screen"
        onKeyDown={onKeyDown}
        className="squircle relative flex max-h-full w-full flex-col overflow-hidden bg-surface shadow-sheet"
        style={{ maxWidth: 'var(--snip-overlay-width)' }}
        initial={{ opacity: 0, scale: enterScale, y: enterY }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98, y: 6 }}
        transition={reduce ? spring.effects : physics.playful}>
        <div className="flex shrink-0 items-center gap-s border-b border-outline-variant/40 px-l py-m">
          <Crop size={16} className="shrink-0 text-on-surface-var" />
          <span data-type="title-l" className="truncate text-on-surface">Crop the capture</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-l py-l">
          {
}
          <div
            ref={stageRef}
            data-snip-stage="true"
            role="group"
            tabIndex={0}
            aria-label="Captured screen. Arrow keys move the selection, Alt with arrow keys resizes it, Enter attaches, Escape cancels."
            onPointerDown={beginDrag}
            className="relative mx-auto block cursor-crosshair select-none overflow-hidden rounded-md outline-none ring-outline-variant/60 focus-visible:ring-2 focus-visible:ring-primary"
            style={{
              aspectRatio: `${Math.max(1, width)} / ${Math.max(1, height)}`,
              width: `min(100%, calc(58vh * ${Math.max(1, width) / Math.max(1, height)}))`,
            }}
          >
            { }
            <img src={frame} alt="" draggable={false} className="pointer-events-none block h-full w-full opacity-40" />
            {
}
            <div className="pointer-events-none absolute overflow-hidden ring-2 ring-primary" style={view.selection}>
              <img src={frame} alt="" draggable={false} className="block" style={view.image} />
            </div>
          </div>
          <p data-type="caption" className="mt-m text-on-surface-low">
            Drag on the capture to select a region, or use the arrow keys (hold Alt to resize).
            {' '}Selection: {Math.round(rect.width)}×{Math.round(rect.height)} px.
          </p>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-s border-t border-outline-variant/40 px-l py-m">
          <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={() => onConfirm(rect)}>Attach selection</Button>
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  )
}
