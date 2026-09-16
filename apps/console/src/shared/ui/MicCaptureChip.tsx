import { Mic } from 'lucide-react'

export function MicCaptureChip({ onStop }: { onStop: () => void }) {
  return (
    <button
      type="button"
      onClick={onStop}
      aria-label="Listening to your microphone — stop recording"
      title="Gideon is listening. Click to stop."
      data-type="caption"
      className="inline-flex min-h-6 -my-px shrink-0 items-center gap-1.5 rounded-pill px-2 py-0.5 transition-colors hover:brightness-110"
      style={{
        background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)',
        color: 'var(--color-warn)',
      }}
    >
      <span className="relative grid size-2.5 place-items-center">
        <span className="size-1.5 rounded-full" style={{ background: 'var(--color-warn)' }} />
        <span
          className="status-pulse absolute inline-flex size-1.5 rounded-full"
          style={{ background: 'var(--color-warn)' }}
        />
      </span>
      <Mic size={12} className="shrink-0" />
      Listening
    </button>
  )
}
