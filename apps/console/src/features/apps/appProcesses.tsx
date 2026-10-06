import { useEffect, useState } from 'react'
import { Server } from 'lucide-react'
import { api, type AppSummary, type AppProcessStatus, type AppProcessExit } from '../../shared/data/api'

function ProcessCard({ title, running, description, exit }: {
  title: string; running: boolean; description?: string; exit?: AppProcessExit | null
}) {
  const ended = running ? null : exit
  return <div className="rounded-md border border-outline-variant bg-surface-high p-m" data-type="body-s">
    <div className="flex items-center gap-2 text-on-surface"><Server size={14} aria-hidden />{title}</div>
    <p className="mt-1 text-on-surface-low">{running ? description || 'Running' : description || 'Not running'}</p>
    {ended && <div className="mt-2" role="status">
      <p className="text-on-surface-low">Exited with code {ended.exitCode}{ended.endedAt ? ` · ${new Date(ended.endedAt).toLocaleString()}` : ''}</p>
      {ended.cause && <p className="mt-1 break-words text-danger">{ended.cause}</p>}
      {!!ended.lines.length && <details className="mt-2 text-on-surface-low">
        <summary className="cursor-pointer">Recent output</summary>
        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-surface p-s" data-type="label-s">{ended.lines.join('\n')}</pre>
      </details>}
    </div>}
  </div>
}

export function ProcessCards({ status, hasBackend }: { status: AppProcessStatus; hasBackend: boolean }) {
  return <div className="space-y-3">
    {hasBackend && <ProcessCard title="Backend" running={status.backendRunning}
      description={status.backendRunning ? `Running on port ${status.backendPort}` : undefined} exit={status.backendExit} />}
    {status.workers?.map(worker => <ProcessCard key={worker.name} title={`Worker · ${worker.name}`}
      running={worker.running} description={!worker.running && worker.reason ? worker.reason : undefined} exit={worker.exit} />)}
    {status.engine && <ProcessCard title="Engine" running={status.engine.running} exit={status.engine.exit} />}
  </div>
}

export function AppProcesses({ app }: { app: AppSummary }) {
  const [status, setStatus] = useState<AppProcessStatus | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    let cancelled = false
    let pending = false
    setStatus(null)
    setError(false)
    async function refresh() {
      if (pending) return
      pending = true
      try {
        const current = await api.app(app.name)
        if (!cancelled) { setStatus(current); setError(false) }
      } catch {
        if (!cancelled) { setStatus(null); setError(true) }
      } finally { pending = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [app.name, app.enabled, app.updatedAt, app.backendRunning])
  if (!status) return <p className="text-on-surface-low" data-type="body-s" role="status">
    {error ? 'Process status is unavailable.' : 'Checking process status…'}
  </p>
  return <ProcessCards status={status} hasBackend={app.hasBackend} />
}
