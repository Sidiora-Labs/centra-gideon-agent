import { useEffect, useReducer, useRef } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api } from '../../shared/data/api'
import { notify } from './appSdk'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { Button } from '../../shared/ui/Button'
import { treatmentPaint } from '../../shared/theme/errorTreatments'
import { useErrorTreatment } from './personality'
import { incidentReducer, initialIncident } from './incidentState'

export function IncidentBanner() {
  const [state, dispatch] = useReducer(incidentReducer, initialIncident)
  const revision = useRef(0), live = useRef(true), resuming = useRef(false)
  const treatment = useErrorTreatment()
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useVisiblePoll(() => {
    if (resuming.current) return
    const observed = ++revision.current
    void api.incident().then((snapshot) => { if (live.current) dispatch({ type: 'snapshot', revision: observed, active: snapshot.active, reason: snapshot.reason }) }).catch(() => {})
  }, 15000)
  const resume = async () => {
    if (resuming.current) return
    resuming.current = true
    const action = ++revision.current
    dispatch({ type: 'resume', revision: action })
    let ok = false
    try { await api.incidentResume(); ok = true }
    catch (error) { notify(`Couldn't resume: ${String((error as Error)?.message || error)}`, 'error') }
    finally {
      resuming.current = false
      if (live.current) dispatch({ type: 'settled', revision: action, ok })
    }
  }
  if (!state.active) return null
  return <div role="alert" className={['flex flex-wrap items-center gap-m border-b border-outline-variant/40 pl-l py-m text-[0.8125rem]', treatment?.surfaceClass].filter(Boolean).join(' ')}
    style={{ background: 'var(--color-error-container)', color: 'var(--color-on-error-container)', paddingRight: 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))', ...treatmentPaint(treatment) }}>
    <AlertTriangle size={16} className={['shrink-0', treatment?.iconClass].filter(Boolean).join(' ')} />
    <span className="min-w-0 flex-1"><strong>Incident mode is active</strong> — all unattended work (cron, hooks, triggers, subagents) is suspended{state.reason ? ` · ${state.reason}` : ''}. Chat still works.</span>
    <Button variant="danger" size="xs" onClick={resume} loading={state.busy} className="shrink-0">Resume</Button>
  </div>
}
