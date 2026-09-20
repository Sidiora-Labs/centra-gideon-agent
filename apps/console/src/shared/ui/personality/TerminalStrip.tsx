
import { useReducedMotion } from '../../theme/motion'

export function TerminalStrip() {
  const reduce = useReducedMotion()

  return (
    <div
      aria-hidden
      data-shell-element="terminal-scanlines"
      className="crt-raster pointer-events-none fixed inset-0 z-[var(--z-overlay)]"
    >
      {!reduce && <div className="crt-beam absolute inset-x-0 top-0 h-[22vh]" />}
    </div>
  )
}
