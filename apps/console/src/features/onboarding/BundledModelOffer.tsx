import { useEffect, useRef, useState } from 'react'
import { api, type AvailableModel, type DownloadJob, type ProviderModels } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { LoadError } from '../../shared/ui/ListScaffold'
import { useModelDownloads } from '../settings/useModelDownloads'
import { setupErrorText } from './essentialSetupState'

export function smallestLocalChatModel(providers: ProviderModels[]): AvailableModel | null {
  return providers.flatMap(provider => provider.local ? provider.models.filter(model =>
    model.capabilities.includes('chat') && !model.downloaded && !model.gated && model.fit !== 'red'
    && model.status !== 'retired' && !model.config_only) : [])
    .sort((a, b) => (a.size_mb || Infinity) - (b.size_mb || Infinity) || a.name.localeCompare(b.name))[0] ?? null
}

export function ModelOfferDetails({ model, job }: { model: AvailableModel; job?: DownloadJob }) {
  return <div className="grid gap-s">
    <p className="text-on-surface">No account? Start with a small offline model</p>
    <p>{model.name} · {model.license || 'License not reported'} · {model.size_mb ? `${Math.round(model.size_mb)} MB` : 'Size not reported'}</p>
    <p className="text-on-surface-low">Downloaded once, then runs locally without a provider account. This is the smallest available chat model; answer quality and supported capabilities depend on the model. {model.description}</p>
    {model.non_commercial && <p>Its license limits commercial use.</p>}
    {job && ['queued', 'running'].includes(job.state) && <div role="status">
      <p>{job.state === 'queued' ? 'Download queued' : `Downloading ${model.name}: ${Math.round(job.progress * 100)}%`}</p>
      <progress aria-label={`Download ${model.name}`} max={1} value={job.progress} />
      <p>{Math.round(job.downloaded_bytes / 1048576)} MB of {Math.round(job.total_bytes / 1048576)} MB{job.eta_s > 0 ? ` · about ${Math.round(job.eta_s)} seconds left` : ''}</p>
    </div>}
    {job?.state === 'done' && <p role="status">Downloaded {model.name} — checking the chat model.</p>}
    {job?.state === 'error' && <p role="alert" className="text-danger">{job.error || job.reason || 'The download failed. Retry the download.'}</p>}
  </div>
}

export function BundledModelOffer({ onReady }: { onReady: () => Promise<void> }) {
  const { data, error, refresh } = useQuery('onboarding:local-chat-catalog', () => api.modelsAvailable())
  const remembered = useRef<AvailableModel | null>(null)
  const offered = smallestLocalChatModel(data ?? [])
  if (!remembered.current && offered) remembered.current = offered
  const model = remembered.current
  const { jobs, start, cancel } = useModelDownloads(model?.provider ?? null, refresh)
  const job = model ? jobs[`${model.provider}:${model.name}`] : undefined
  const handled = useRef(new Set<string>())
  const [failure, setFailure] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!model || job?.state !== 'done' || handled.current.has(job.id)) return
    handled.current.add(job.id)
    let alive = true
    void (async () => {
      try {
        const available = await api.modelsAvailable()
        const downloaded = available.flatMap(provider => provider.models).find(row => row.provider === model.provider && row.name === model.name)
        if (!downloaded?.downloaded) throw new Error('The completed download is not present on this device. Retry the download before checking chat.')
        const current = await api.activeModels()
        if (!current.use_cases.chat?.length) {
          await api.setActiveModel('chat', [`${model.provider}:${model.name}`], current.revisions.chat)
        }
        await onReady()
      } catch (error) { if (alive) setFailure(setupErrorText(error)) }
    })()
    return () => { alive = false }
  }, [job, model, onReady])
  if (!data && error) return <LoadError what="local model offer" error={error} onRetry={refresh} />
  if (!model) return null
  const active = job && ['queued', 'running'].includes(job.state)
  const act = async (action: 'start' | 'cancel') => {
    if (busy) return
    setBusy(true); setFailure('')
    try { if (action === 'start') await start(model.name); else await cancel(model.name) }
    catch (error) { setFailure(setupErrorText(error)) }
    finally { setBusy(false) }
  }
  return <div role="group" aria-label={`Download ${model.name}`} className="grid gap-s rounded-lg border border-outline-variant bg-surface-high p-m">
    <ModelOfferDetails model={model} job={job} />
    {(job?.state !== 'done' || Boolean(failure)) && <Button variant="secondary" size="sm" loading={busy} onClick={() => void act(active ? 'cancel' : 'start')}>
      {active ? 'Cancel the model download' : `Download ${model.name}`}
    </Button>}
    {failure && <div role="alert" className="grid gap-s text-danger"><p>{failure}</p><Button variant="secondary" size="sm" onClick={() => void onReady()}>Retry model check</Button></div>}
  </div>
}
