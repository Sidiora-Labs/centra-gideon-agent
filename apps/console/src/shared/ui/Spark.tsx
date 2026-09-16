import { useId } from 'react'
import { GideonMark } from './GideonMark'

const SCHEME_GRADIENT =
  'linear-gradient(135deg, var(--grad-1), var(--grad-2), var(--grad-3), var(--grad-4))'

/** The supplied Gideon mark for thinking indicators, loop nodes, and empty states. */
export function Spark({ size = 24, animated = true }: { size?: number; animated?: boolean }) {
  const id = useId().replace(/:/g, '')
  return <GideonMark size={size} animated={animated} idGradient={`spark-${id}`} />
}

/** Supplied Gideon mark beside the scheme-colored product name. */
export function Wordmark({ label = 'Gideon' }: { label?: string }) {
  return (
    <span className="flex items-center gap-s">
      <GideonMark size={22} idGradient="wordmark-grad" />
      <span
        className="text-on-surface"
        data-type="title-l"
        style={{ background: SCHEME_GRADIENT, WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}
      >
        {label}
      </span>
    </span>
  )
}
