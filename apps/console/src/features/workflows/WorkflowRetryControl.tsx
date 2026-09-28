import { useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { api, requireWriteAccepted, type WorkflowRunDetailData } from '../../shared/data/api'
import { QuietButton } from '../../shared/ui/QuietButton'
import { retryWindow } from './retryMeta'

export function WorkflowRetryControl({ run, onFresh, onOpenRun, onError }: {
  run: WorkflowRunDetailData
  onFresh: (run: WorkflowRunDetailData) => void
  onOpenRun: (id: string) => void
  onError: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const window = retryWindow(run)
  const remaining = Math.max(0, Math.ceil((window?.retryAt ?? 0) - now))
  useEffect(() => {
    if (!remaining) return
    const timer = globalThis.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => globalThis.clearInterval(timer)
  }, [remaining])
  if (!window) return null
  const retry = async () => {
    if (busy || remaining) return
    setBusy(true)
    let child = ''
    try {
      const fresh = await api.workflowRun(run.run_id)
      onFresh(fresh)
      const current = retryWindow(fresh)
      if (!current) { onError('This run no longer has a retryable failure. Review the current diagnosis.'); return }
      if (current.retryAt > Date.now() / 1000) { setNow(Date.now() / 1000); return }
      const fork = await api.forkWorkflowRun(run.run_id, { note: 'Retry after transient failure' })
      requireWriteAccepted(fork)
      child = fork.child_run_id
      requireWriteAccepted(await api.startWorkflowDraft(child))
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Could not retry this run.')
    } finally {
      if (child) onOpenRun(child)
      setBusy(false)
    }
  }
  const reason = busy ? 'Preparing retry…' : remaining ? `Retry becomes available in ${remaining}s` : undefined
  return <div className="flex flex-col items-end gap-xs">
    <QuietButton onClick={() => void retry()} disabled={busy || remaining > 0} disabledReason={reason}
      title={reason || 'Retry this failed run in a new run'}><RotateCcw size={13} /> Retry</QuietButton>
    {remaining > 0 && <span data-type="caption" className="text-on-surface-low">Calls to this provider are paused after repeated failures. Retry becomes available in {remaining}s.</span>}
  </div>
}
