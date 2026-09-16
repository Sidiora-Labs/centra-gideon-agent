import { useEffect, useRef } from 'react'

export function startVisiblePolling(callback: () => void, interval: number): () => void {
  let timer: ReturnType<typeof setInterval> | undefined
  const pause = () => {
    if (timer !== undefined) clearInterval(timer)
    timer = undefined
  }
  const resume = () => {
    pause()
    if (!document.hidden) timer = setInterval(() => { if (!document.hidden) callback() }, interval)
  }
  const visibility = () => {
    if (document.hidden) pause()
    else { callback(); resume() }
  }
  callback()
  resume()
  document.addEventListener('visibilitychange', visibility)
  return () => { pause(); document.removeEventListener('visibilitychange', visibility) }
}

export function useVisiblePoll(callback: () => void, interval: number | null): void {
  const latest = useRef(callback)
  latest.current = callback
  useEffect(() => interval === null ? undefined : startVisiblePolling(() => latest.current(), interval), [interval])
}
