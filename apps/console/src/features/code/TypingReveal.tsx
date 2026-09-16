import { useEffect, useRef, useState } from 'react'

const MIN_MS = 450
const MAX_MS = 1500
const FRAME_MS = 16

export function TypingReveal({ text, mode, theme, onDone }: {
  text: string
  mode: 'write' | 'erase'
  theme: 'light' | 'dark'
  onDone?: () => void
}) {
  const total = text.length
  const [shown, setShown] = useState(mode === 'write' ? 0 : total)
  const doneRef = useRef(onDone); doneRef.current = onDone
  const scrollRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const reduce = typeof window !== 'undefined'
      && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce || total === 0) {
      setShown(mode === 'write' ? total : 0)
      doneRef.current?.()
      return
    }
    const durationMs = Math.min(MAX_MS, Math.max(MIN_MS, total * 3))
    const ticks = Math.max(1, Math.round(durationMs / FRAME_MS))
    const perTick = Math.max(1, Math.ceil(total / ticks))
    let raf = 0
    let last = 0
    let n = mode === 'write' ? 0 : total
    const step = (ts: number) => {
      if (!last) last = ts
      if (ts - last >= FRAME_MS) {
        last = ts
        n = mode === 'write' ? Math.min(total, n + perTick) : Math.max(0, n - perTick)
        setShown(n)
        if ((mode === 'write' && n >= total) || (mode === 'erase' && n <= 0)) {
          doneRef.current?.()
          return
        }
      }
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [text, mode, total])

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }) }, [shown])

  const visible = text.slice(0, shown)
  const lines = visible.split('\n')
  const bg = theme === 'light' ? '#ffffff' : '#1e1e1e'
  const fg = theme === 'light' ? '#1f1f1f' : '#d4d4d4'
  const gutter = theme === 'light' ? '#9aa0a6' : '#6e7681'
  return (
    <pre ref={scrollRef} aria-label={mode === 'erase' ? 'Deleting file' : 'Writing file'}
      className="m-0 h-full overflow-auto p-0 font-mono text-[13px] leading-[1.5]"
      style={{ background: bg, color: fg, tabSize: 2 }}>
      <code className="block px-0 py-2.5">
        {lines.map((ln, i) => (
          <span key={i} className="grid grid-cols-[3.5ch_1fr] px-3">
            <span className="select-none pr-3 text-right" style={{ color: gutter }}>{i + 1}</span>
            <span className="whitespace-pre-wrap break-words">
              {ln}
              { }
              {i === lines.length - 1 && shown < total && (
                <span className="reveal-caret inline-block" style={{ background: fg, width: '0.6ch', height: '1em', verticalAlign: 'text-bottom' }} />
              )}
            </span>
          </span>
        ))}
      </code>
    </pre>
  )
}
