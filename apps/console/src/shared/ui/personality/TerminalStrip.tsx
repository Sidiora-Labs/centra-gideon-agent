
import { useEffect, useState } from 'react'
import { prefersReducedMotion } from '../../theme/motion'

const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'

export function TerminalStrip() {
  const [reduce, setReduce] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(REDUCE_QUERY)
    const onChange = () => setReduce(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])

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
