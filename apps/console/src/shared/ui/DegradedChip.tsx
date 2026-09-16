import { useEffect, useId, useReducer, useRef, useState, type RefObject } from 'react'
import { ArrowRight, CloudOff, Sparkles } from 'lucide-react'
import { api } from '../data/api'
import { useVisiblePoll } from '../data/useVisiblePoll'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { TextLink } from './TextLink'
import { useDismissKey } from './overlayInteraction'
import { degradedPresentation, degradedReading, initialDegradedReading, missingModelLabel, surfaceLabel } from './statusSurfaceState'

function DegradedPanel({ state, close, trigger, id }: {
  state: ReturnType<typeof degradedPresentation>; close: () => void; trigger: RefObject<HTMLButtonElement | null>; id: string
}) {
  const restore = () => { close(); trigger.current?.focus() }
  useDismissKey('Escape', restore, 100)
  return <>
    <div className="fixed inset-0 z-40" onClick={close} aria-hidden />
    <div id={id} role="dialog" aria-label="Degraded surfaces"
      className="absolute right-0 z-50 mt-1.5 max-h-[min(70vh,36rem)] w-80 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-outline-variant/40 bg-surface-container p-3 shadow-lg">
      <div data-type="body-s" className="mb-2 flex items-center gap-1.5 font-medium text-warn">
        <CloudOff size={14} aria-hidden />{state.unknown ? 'Could not read the check' : 'Running without a model'}
      </div>
      {state.unknown && <div data-type="body-s" className="text-on-surface-low">
        The degraded-surfaces check is not answering, so this chip cannot say whether any surface is running without a model. It will clear itself when the check responds.
      </div>}
      {state.down.length > 0 && <div className="mb-2 border-b border-outline-variant/30 pb-2">
        <TextLink href="#/settings/models" icon={ArrowRight} iconPosition="trailing" size="xs" onClick={restore}>Bind a model in Settings → Models</TextLink>
      </div>}
      <div className="flex flex-col gap-2">{state.down.map(surface => {
        const missing = (surface.use_cases ?? []).map(missingModelLabel).join(', ')
        return <div key={surface.surface} className="rounded-lg bg-surface/50 px-2 py-2">
          <div className="flex items-center justify-between gap-2">
            <span data-type="body-s" className="text-on-surface">{surfaceLabel(surface.surface)}</span>
            {surface.backlog > 0 && <span data-type="caption" className="shrink-0 text-on-surface-low">{surface.backlog} queued</span>}
          </div>
          {missing && <div data-type="caption" className="mt-0.5 text-on-surface-var">No model for {missing}</div>}
          <div data-type="caption" className="mt-0.5 text-on-surface-low">{surface.floor}</div>
        </div>
      })}</div>
    </div>
  </>
}

export function DegradedChip() {
  const [reading, dispatch] = useReducer(degradedReading, initialDegradedReading)
  const [open, setOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const id = useId()
  const alive = useRef(true)
  const latest = useRef(0)
  const isMobile = useIsMobile()
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useVisiblePoll(() => {
    const request = ++latest.current
    const publish = (event: Parameters<typeof degradedReading>[1]) => {
      if (alive.current && request === latest.current) dispatch(event)
    }
    api.degraded().then(result => publish({ type: 'surfaces', surfaces: result.surfaces }))
      .catch(() => publish({ type: 'failure' }))
    api.onboarding().then(result => publish({ type: 'provider', value: result.has_model_provider }))
      .catch(() => publish({ type: 'provider', value: null }))
  }, 20000)
  const state = degradedPresentation(reading)
  if (!state.visible) return null
  const Icon = state.setup ? Sparkles : CloudOff
  const skin = state.setup
    ? { background: 'color-mix(in srgb, var(--color-info) 16%, transparent)', color: 'var(--color-info)' }
    : { background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }
  return <div className="relative">
    <button ref={triggerRef} type="button" onClick={() => setOpen(value => !value)} data-type="caption"
      className={`flex min-h-6 items-center gap-1.5 rounded-pill py-1 font-medium transition-colors hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${isMobile ? 'px-1.5' : 'px-2.5'}`}
      style={skin} aria-expanded={open} aria-controls={open ? id : undefined} aria-haspopup="dialog"
      aria-label={isMobile ? state.summary : undefined} title={state.detail}>
      <Icon size={13} className="shrink-0" aria-hidden />{!isMobile && <span>{state.summary}</span>}
    </button>
    {open && <DegradedPanel state={state} close={() => setOpen(false)} trigger={triggerRef} id={id} />}
  </div>
}
