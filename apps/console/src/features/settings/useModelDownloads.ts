import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type DownloadJob } from '../../shared/data/api'

export function useModelDownloads(provider: string, onSettled: () => void) {
  const [jobs, setJobs] = useState<Record<string, DownloadJob>>({})
  const streams = useRef<Map<string, EventSource>>(new Map())
  const settled = useRef(onSettled)
  settled.current = onSettled

  const closeStream = useCallback((id: string) => {
    streams.current.get(id)?.close()
    streams.current.delete(id)
  }, [])

  const attach = useCallback((job: DownloadJob) => {
    setJobs((prev) => ({ ...prev, [job.model]: job }))
    if (job.state !== 'running' || streams.current.has(job.id)) return
    let es: EventSource
    try { es = new EventSource(api.downloadStreamUrl(job.id)) } catch { return }
    streams.current.set(job.id, es)
    const onFrame = (e: Event) => {
      let data: DownloadJob | null = null
      try { data = JSON.parse((e as MessageEvent).data) as DownloadJob } catch { return }
      if (!data) return
      setJobs((prev) => ({ ...prev, [data!.model]: data! }))
      if (data.state !== 'running') { closeStream(data.id); settled.current() }
    }
    for (const ev of ['snapshot', 'progress', 'done', 'error', 'cancelled']) es.addEventListener(ev, onFrame)
    es.onerror = () => {   }
  }, [closeStream])

  useEffect(() => {
    let alive = true
    api.modelDownloads().then((all) => {
      if (!alive) return
      all.filter((j) => j.provider === provider).forEach(attach)
    }).catch(() => {   })
    const map = streams.current
    return () => { alive = false; map.forEach((es) => es.close()); map.clear() }
  }, [provider, attach])

  const start = useCallback(async (model: string) => {
    const job = await api.startModelDownload(provider, model)
    attach(job)
    if (job.state !== 'running') settled.current()
  }, [provider, attach])

  const cancel = useCallback(async (model: string) => {
    const job = jobs[model]
    if (!job) return
    await api.cancelModelDownload(job.id)
    closeStream(job.id)
    setJobs((prev) => { const n = { ...prev }; delete n[model]; return n })
    settled.current()
  }, [jobs, closeStream])

  return { jobs, start, cancel }
}
