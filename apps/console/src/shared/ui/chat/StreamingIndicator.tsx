import { useEffect, useState } from 'react'
import { useReducedMotion } from '../../theme/motion'
import { GenerationLoader } from '../../vendor/assistant-ui/elements/loading-state'
import { ThinkingIndicator } from '../../vendor/assistant-ui/elements/thinking-indicator'
import { StreamingText } from '../../vendor/assistant-ui/elements/streaming-text'
import { TypingIndicator } from '../../vendor/assistant-ui/elements/typing-indicator'

interface Props {
  statusText: string
  activity: string | null
}

export function StreamingIndicator({ statusText, activity }: Props) {
  const reducedMotion = useReducedMotion()
  const [tick, setTick] = useState(0)
  const status = statusText.trim()
  useEffect(() => {
    if (status || reducedMotion) return
    const timer = window.setInterval(() => setTick((value) => value + 1), 350)
    return () => window.clearInterval(timer)
  }, [status, reducedMotion])
  const currentActivity = activity?.trim() || ''
  const responding = /\b(respond|writ|stream)/i.test(status)
  const words = currentActivity ? currentActivity.split(' ').length : 0

  return (
    <div className="flex min-w-0 items-start gap-3 py-2" role="status" aria-live="polite" data-testid="streaming-indicator">
      {reducedMotion ? <span>{status || "Working"}</span> : responding ? <><TypingIndicator variant="bare" /><span>{status}</span></> : status ? <ThinkingIndicator label={status} /> : <GenerationLoader label="Working" tick={tick} />}
      {currentActivity && (
        reducedMotion ? <span className="min-h-0 max-w-none text-on-surface-low">{currentActivity}</span> : <StreamingText segments={[{ text: currentActivity }]} count={words} streaming className="min-h-0 max-w-none text-on-surface-low" />
      )}
    </div>
  )
}
