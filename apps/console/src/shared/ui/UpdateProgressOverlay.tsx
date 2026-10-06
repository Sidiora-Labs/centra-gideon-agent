import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Check, CheckCircle2, DownloadCloud, Loader2, RefreshCw } from 'lucide-react'
import { spring, physics } from '../theme/motion'
import { useChatSocket } from '../data/useChatSocket'
import { api } from '../data/api'
import { useFocusTrap } from './useFocusTrap'
import { accentChip } from '../theme/accent'


const STEPS = [
  { id: 'pulling', label: 'Pulling' },
  { id: 'installing', label: 'Installing dependencies' },
  { id: 'building', label: 'Building frontend' },
  { id: 'restarting', label: 'Restarting' },
] as const

type StepId = (typeof STEPS)[number]['id']
type Phase = StepId | 'rolling_back' | 'stopping' | 'done' | 'error' | 'cancelled'
const STEP_IDS = new Set<string>(STEPS.map((s) => s.id))
const PRE_RESTART_STEPS = new Set(['pulling', 'installing', 'building'])

export interface UpdateProgress {
  phase: Phase
  detail: string
  restartOnly: boolean
}

export function useUpdateProgress() {
  const [state, setState] = useState<UpdateProgress | null>(null)
  const seenPreRestartStep = useRef(false)
  const [busy, setBusy] = useState(false)

  const apply = useCallback((step: string, detail: string) => {
    if (step === 'done') {
      seenPreRestartStep.current = false
      setState({ phase: 'done', detail: detail || 'Update complete', restartOnly: false })
    } else if (step === 'cancelled') {
      setState({ phase: 'cancelled', detail, restartOnly: false })
    } else if (step === 'rolling_back') {
      setState({ phase: 'rolling_back', detail, restartOnly: false })
    } else if (step === 'error' || step === 'failed') {
      setState((prev) => ({ phase: 'error', detail: detail || 'Update failed', restartOnly: prev?.restartOnly ?? false }))
    }
    else if (step === 'warning') setState((p) => (p ? { ...p, detail } : p))
    else if (STEP_IDS.has(step)) {
      if (PRE_RESTART_STEPS.has(step)) seenPreRestartStep.current = true
      const isRestartOnly = step === 'restarting' && !seenPreRestartStep.current
      setState({ phase: step as StepId, detail, restartOnly: isRestartOnly })
    }
  }, [])

  useChatSocket(
    useCallback((m) => {
      if (m.type !== 'update_progress') return
      const d = m.data || {}
      apply(String(d.step ?? ''), String(d.detail ?? ''))
    }, [apply]),
    useCallback(() => {
      setState((p) => {
        if (p && p.phase === 'restarting') {
          seenPreRestartStep.current = false
          return { phase: 'done', detail: p.restartOnly ? 'Restart complete' : 'Update complete', restartOnly: p.restartOnly }
        }
        return p
      })
    }, []),
  )

  useEffect(() => {
    api.status().then((s) => {
      const p = s.update_progress
      if (p && typeof p.step === 'string' && p.step) apply(p.step, String(p.detail ?? ''))
    }).catch(() => {})
  }, [apply])

  useEffect(() => {
    if (state?.phase !== 'done') return
    const t = window.setTimeout(() => setState(null), 2000)
    return () => window.clearTimeout(t)
  }, [state?.phase])

  const cancel = useCallback(async () => {
    setBusy(true)
    try {
      const result = await api.cancelUpdate()
      if (result.status === 'stopped') {
        setState((prev) => prev?.phase === 'cancelled' ? prev : { phase: 'cancelled', detail: 'Update stopped. Review update recovery before restarting.', restartOnly: false })
      } else if (result.status === 'stopping') {
        setState((prev) => prev ? { ...prev, phase: 'stopping', detail: 'Stopping update processes…' } : prev)
      } else {
        setState((prev) => prev ? { ...prev, phase: 'error', detail: 'No update operation is running. Check update recovery or dismiss this message.' } : prev)
      }
    } catch (error) {
      setState((prev) => prev ? { ...prev, detail: String(error) } : prev)
    } finally { setBusy(false) }
  }, [])

  const dismiss = useCallback(async () => {
    setBusy(true)
    try {
      await api.dismissUpdate()
      seenPreRestartStep.current = false
      setState(null)
    } catch (error) {
      setState((prev) => prev ? { ...prev, detail: String(error) } : prev)
    } finally { setBusy(false) }
  }, [])

  const hide = useCallback(() => setState(null), [])
  return { progress: state, cancel, dismiss, hide, busy }

}

function restartOnlyDetail(detail: string): string {
  const d = detail.replace(/\s*—\s*restarting…?\s*$/i, '').trim()
  return d && !/^restarting/i.test(d) ? `${d} — reconnecting shortly…` : 'Reconnecting shortly…'
}

function StepRow({ label, status }: { label: string; status: 'done' | 'active' | 'pending' }) {
  return (
    <div className="flex items-center gap-3">
      <span className="grid size-6 shrink-0 place-items-center rounded-full"
        style={status === 'done' ? { background: 'color-mix(in srgb, var(--color-success) 18%, transparent)', color: 'var(--color-success)' }
          : status === 'active' ? accentChip
          : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        {status === 'done' ? <Check size={13} />
          : status === 'active' ? <Loader2 size={13} className="animate-spin" />
          : <span className="size-1.5 rounded-full bg-current opacity-60" />}
      </span>
      <span data-type="body-s"
        style={{ color: status === 'pending' ? 'var(--color-on-surface-low)' : 'var(--color-on-surface)' }}>
        {label}
      </span>
    </div>
  )
}

export function UpdateProgressOverlay() {
  const { progress, cancel, dismiss, hide, busy } = useUpdateProgress()
  return createPortal(
    <AnimatePresence>
      {progress && <UpdateSheet progress={progress} cancel={cancel} dismiss={dismiss} hide={hide} busy={busy} />}
    </AnimatePresence>,
    document.body,
  )
}

function UpdateSheet({ progress, cancel, dismiss, hide, busy }: { progress: UpdateProgress; cancel: () => void; dismiss: () => void; hide: () => void; busy: boolean }) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const stepIdx = STEPS.findIndex((s) => s.id === progress.phase)
  const isCancelled = progress.phase === 'cancelled'
  const isError = progress?.phase === 'error'
  const canCancel = ['pulling', 'installing', 'rolling_back'].includes(progress.phase) && !progress.restartOnly
  const isDone = progress?.phase === 'done'
  const isRestartOnly = progress?.restartOnly ?? false

  const title = progress.phase === 'stopping' ? 'Stopping update' : isCancelled ? 'Update stopped' : isError
    ? (isRestartOnly ? 'Restart failed' : 'Update failed')
    : isDone
      ? (isRestartOnly ? 'Restart complete' : 'Update complete')
      : isRestartOnly
        ? 'Restarting gateway'
        : 'Updating Gideon'

  return (
    <motion.div key="update-overlay" className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-2xl"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
          <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
          <motion.div ref={trapRef} role="alertdialog" aria-modal="true" aria-label="Update progress"
            className="relative w-full max-w-[400px] overflow-hidden rounded-xl bg-surface shadow-sheet"
            initial={{ opacity: 0, scale: 0.97, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 6 }} transition={physics.playful}>
            <div className="flex items-start gap-3 px-l pt-l">
              <span className="mt-0.5 shrink-0" style={{ color: isError ? 'var(--color-danger)' : isDone ? 'var(--color-success)' : 'var(--color-primary)' }}>
                {isError ? <AlertTriangle size={18} />
                  : isDone ? <CheckCircle2 size={18} />
                  : isRestartOnly ? <RefreshCw size={18} className="animate-spin" />
                  : <DownloadCloud size={18} />}
              </span>
              <div className="min-w-0 flex-1" aria-live="polite" aria-atomic="true">
                <div data-type="title-l" className="text-on-surface">{title}</div>
                {progress.detail && (
                  <div data-type="body-s" className="mt-1" style={{ color: isError ? 'var(--color-danger)' : 'var(--color-on-surface-var)' }}>
                    {isRestartOnly && !isError && !isDone ? restartOnlyDetail(progress.detail) : progress.detail}
                  </div>
                )}
              </div>
            </div>

            { }
            {!isError && !isCancelled && !isRestartOnly && (
              <div className="mt-4 flex flex-col gap-2.5 px-l">
                {STEPS.map((s, i) => (
                  <StepRow key={s.id} label={s.label}
                    status={isDone || i < stepIdx ? 'done' : i === stepIdx ? 'active' : 'pending'} />
                ))}
              </div>
            )}

            <div className="flex justify-end px-l py-l">
              {!isDone && (
                <button type="button" onClick={isError || isCancelled ? dismiss : canCancel ? cancel : hide} disabled={busy} data-type="body-s"
                  className="rounded-pill px-4 h-9 text-on-surface-var bg-surface-high hover:bg-surface-highest transition-colors">
                  {busy ? 'Please wait…' : isError || isCancelled ? 'Dismiss' : canCancel ? 'Cancel update' : 'Hide'}
                </button>
              )}
            </div>
      </motion.div>
    </motion.div>
  )
}
