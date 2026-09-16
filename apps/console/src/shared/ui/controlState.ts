import type { MouseEvent } from 'react'
import { expr } from '../theme/motion'

export function controlAvailability(disabled = false, loading = false, reason?: string, reachable = false) {
  const blocked = disabled || loading
  const nativeDisabled = blocked && !reachable && (loading || !reason)
  return { blocked, nativeDisabled, ariaDisabled: disabled && !nativeDisabled || undefined, busy: loading || undefined }
}

export function controlTitle(title: string | undefined, disabled: boolean, reason?: string) {
  return disabled && reason ? [title, reason].filter(Boolean).join(' — ') : title
}

export function activateControl<E extends MouseEvent>(event: E, blocked: boolean, action?: (event: E) => void) {
  if (blocked) { event.preventDefault(); return }
  action?.(event)
}

export function controlMotion(reduced: boolean | null, blocked: boolean, press: number, hover = 0) {
  return {
    whileTap: blocked ? undefined : { scale: reduced ? 1 : 1 - expr(press, 0.4) },
    whileHover: blocked || hover === 0 ? undefined : { scale: reduced ? 1 : 1 + expr(hover, 0.35) },
  }
}

export function pointerPercent(position: number, start: number, length: number) {
  return length > 0 ? Math.min(100, Math.max(0, (position - start) / length * 100)) : 50
}

export function segmentTarget(key: string, index: number, count: number): number | null {
  if (count < 1) return null
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  const steps: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }
  const direction = steps[key]
  return direction === undefined ? null : (index + direction + count) % count
}
