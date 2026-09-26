import { useState } from 'react'
import { ThinkingIndicator } from '../../shared/vendor/assistant-ui/elements/thinking-indicator'
import { ReasoningRoot, ReasoningTrigger, ReasoningContent, ReasoningText } from '../../shared/vendor/assistant-ui/elements/reasoning'

export function ThinkingBlock({ text, defaultOpen, connected = false, streaming = false }: { text: string; defaultOpen?: boolean; connected?: boolean; streaming?: boolean }) {
  const [openInit] = useState(!!defaultOpen)
  if (connected) return (
    <ReasoningRoot defaultOpen={openInit} streaming={streaming} className="my-1">
      <ReasoningTrigger active={streaming} label="Thinking" />
      <ReasoningContent><ReasoningText className="whitespace-pre-wrap break-words">{text}</ReasoningText></ReasoningContent>
    </ReasoningRoot>
  )
  return (
    <details data-testid="thinking-block" open={openInit} data-type="caption"
      className="my-1 rounded-md border border-outline/40 bg-surface-high/40 px-2 py-1 text-on-surface-low">
      <summary className="cursor-pointer select-none opacity-80"><ThinkingIndicator label="Thinking" /></summary>
      <div className="mt-1 whitespace-pre-wrap break-words">{text}</div>
    </details>
  )
}
