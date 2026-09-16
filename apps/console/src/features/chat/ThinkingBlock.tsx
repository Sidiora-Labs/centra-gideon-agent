import { useState } from 'react'

export function ThinkingBlock({ text, defaultOpen }: { text: string; defaultOpen?: boolean }) {
  const [openInit] = useState(!!defaultOpen)
  return (
    <details data-testid="thinking-block" open={openInit} data-type="caption"
      className="my-1 rounded-md border border-outline/40 bg-surface-high/40 px-2 py-1 text-on-surface-low">
      <summary className="cursor-pointer select-none opacity-80">Thinking</summary>
      <div className="mt-1 whitespace-pre-wrap break-words">{text}</div>
    </details>
  )
}
