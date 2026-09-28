import { StartedByApp } from './StartedByApp'

export interface ChatContextLineProps {
  startedBy?: { name: string; destination: string } | null
  navigate: (path: string) => void
}

/** Wrapping context row below the chat title; absent origin leaves no extra header row. */
export function ChatContextLine({ startedBy, navigate }: ChatContextLineProps) {
  if (!startedBy?.name.trim() || !startedBy.destination.trim()) return null

  return (
    <div data-header-below className="flex min-w-0 flex-wrap items-center gap-1.5">
      <StartedByApp name={startedBy.name} destination={startedBy.destination} navigate={navigate} />
    </div>
  )
}
