import { useId } from 'react'
import { GideonMark } from './GideonMark'

/** A scheme-driven brand gradient built from the active scheme's --grad tokens
 *  (re-tinted per scheme by the appearance store), so the wordmark tracks the theme
 *  instead of a hardcoded color family. */
const SCHEME_GRADIENT =
  'linear-gradient(135deg, var(--grad-1), var(--grad-2), var(--grad-3), var(--grad-4))'

/** The Gideon brand mark used as the AI motif throughout the app. Renders
 *  the gideon logo painted with the ACTIVE scheme gradient — NOT the Gemini sparkle.
 *  Kept named `Spark` so all call sites (thinking indicator, loop cycle nodes,
 *  empty states) get the gideon without churn. */
export function Spark({ size = 24, animated = true }: { size?: number; animated?: boolean }) {
  const id = useId().replace(/:/g, '') // unique gradient id per instance
  return <GideonMark size={size} animated={animated} idGradient={`spark-${id}`} />
}

/** Inline wordmark lockup: gideon mark + name (both track the active scheme). */
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
