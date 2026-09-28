import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type DownloadJob } from '../../shared/data/api'

export function useModelDownloads(provider: string | null, onSettled: () => void) {
  const [jobs, setJobs] = useState<Record<string, DownloadJob>>({})
  const streams = useRef<Map<string, EventSource>>(new Map())
  const settled = useRef(onSettled)
  settled.current = onSettled

  const closeStream = useCallback((id: string) => {
    streams.current.get(id)?.close()
    streams.current.delete(id)
  }, [])

  const attach = useCallback((job: DownloadJob) => {
    const key = `${job.provider}:${job.model}`
    setJobs((prev) => ({ ...prev, [key]: job }))
    if (!['queued', 'running'].includes(job.state) || streams.current.has(job.id)) return
    let es: EventSource
    try { es = new EventSource(api.downloadStreamUrl(job.id)) } catch { return }
    streams.current.set(job.id, es)
    const onFrame = (e: Event) => {
      let data: DownloadJob | null = null
      try { data = JSON.parse((e as MessageEvent).data) as DownloadJob } catch { return }
      if (!data) return
      setJobs((prev) => ({ ...prev, [`${data!.provider}:${data!.model}`]: data! }))
      if (!['queued', 'running'].includes(data.state)) { closeStream(data.id); settled.current() }
    }
    for (const ev of ['snapshot', 'progress', 'done', 'error', 'cancelled']) es.addEventListener(ev, onFrame)
    es.onerror = () => {   }
  }, [closeStream])

  useEffect(() => {
    let alive = true
    api.modelDownloads().then((all) => {
      if (!alive) return
      all.filter((j) => provider == null || j.provider === provider).forEach(attach)
    }).catch(() => {   })
    const map = streams.current
    return () => { alive = false; map.forEach((es) => es.close()); map.clear() }
  }, [provider, attach])

  const start = useCallback(async (model: string, providerOverride?: string) => {
    const selectedProvider = providerOverride ?? provider
    if (!selectedProvider) throw new Error('A local model provider is required.')
    const job = await api.startModelDownload(selectedProvider, model)
    attach(job)
    if (!['queued', 'running'].includes(job.state)) settled.current()
  }, [provider, attach])

  const cancel = useCallback(async (model: string, providerOverride?: string) => {
    const selectedProvider = providerOverride ?? provider
    const job = jobs[`${selectedProvider}:${model}`]
    if (!job) return
    await api.cancelModelDownload(job.id)
    closeStream(job.id)
    setJobs((prev) => { const n = { ...prev }; delete n[`${selectedProvider}:${model}`]; return n })
    settled.current()
  }, [jobs, closeStream, provider])

  return { jobs, start, cancel }
}
