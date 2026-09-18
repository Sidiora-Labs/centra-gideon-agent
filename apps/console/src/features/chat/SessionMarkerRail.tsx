import type { SessionMarker } from './sessionMarkers'

export const RAIL_MIN_MARKERS = 4

const KIND_LABEL: Record<SessionMarker['kind'], string> = {
  turn: 'Turn',
  tool: 'Tool',
  subagent: 'Subagent',
  approval: 'Approval',
  error: 'Error',
  activity: 'Activity',
}

export function markerAriaLabel(m: SessionMarker): string {
  const head = m.kind === 'turn' ? (m.role === 'user' ? 'You' : 'Assistant') : KIND_LABEL[m.kind]
  return `${head}: ${m.label}${m.failedTool ? ' (failed tool)' : ''}`
}

export function SessionMarkerRail({ markers, currentTurn, onJump, className = '' }: {
  markers: SessionMarker[]
  currentTurn: number
  onJump: (marker: SessionMarker) => void
  className?: string
}) {
  if (markers.length < RAIL_MIN_MARKERS) return null
  return (
    <nav aria-label="Session index" data-testid="session-marker-rail"
      className={`pointer-events-auto flex flex-col items-end gap-[3px] ${className}`}>
      {markers.map((m) => {
        const current = m.turnIndex === currentTurn
        const label = markerAriaLabel(m)
        return (
          <button key={m.id} type="button" aria-label={label} title={label}
            aria-current={current ? 'true' : undefined}
            data-kind={m.kind} data-state={current ? 'current' : 'historical'}
            onClick={() => onJump(m)}
            className={`h-[3px] cursor-pointer rounded-pill border-none p-0 transition-colors duration-150 ${
              m.kind === 'turn' ? 'w-4' : 'w-2.5'
            } ${
              current
                ? 'bg-primary opacity-100'
                : m.failedTool
                  ? 'bg-danger opacity-70'
                  : 'bg-on-surface-low opacity-40'
            }`} />
        )
      })}
    </nav>
  )
}
