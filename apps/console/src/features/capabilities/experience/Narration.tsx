import { useEffect, useRef, useState } from 'react'
import { gatewayRequest, requestJson, responseError } from '../../../shared/data/gatewayRequest'
import OwnedAudio from './OwnedAudio'
import { Button } from '../../../shared/ui/Button'

type Job = { id: string; status: string; error: string; node_id: string }
export default function Narration({ sessionId, revision, baseUrl = '/api/capabilities/experience' }: { sessionId: string; revision: number; baseUrl?: string }) {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [audio, setAudio] = useState('')
  const pending = useRef('')
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    setJob(null); setError(''); setAudio(''); setBusy(false); pending.current = ''
  }, [sessionId, revision])
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return
    let active = true
    const timer = setTimeout(() => {
      requestJson<{ narration: Job }>(`${baseUrl}/narrations/${job.id}`).then(result => { if (active) setJob(result.narration) }).catch(cause => { if (active) setError(String(cause)) })
    }, 350)
    return () => { active = false; clearTimeout(timer) }
  }, [job, baseUrl])
  useEffect(() => {
    if (job?.status !== 'ready') return
    let active = true, url = ''
    gatewayRequest(`${baseUrl}/narrations/${job.id}/audio`).then(async response => {
      if (!response.ok) throw await responseError(response)
      const blob = await response.blob()
      if (active) { url = URL.createObjectURL(blob); setAudio(url) }
    }).catch(cause => { if (active) setError(String(cause)) })
    return () => { active = false; if (url) URL.revokeObjectURL(url) }
  }, [job?.id, job?.status, baseUrl])
  const start = async () => {
    const current = generation.current
    setBusy(true); setError('')
    pending.current ||= crypto.randomUUID().replaceAll('-', '')
    try {
      const result = await requestJson<{ narration: Job }>(`${baseUrl}/sessions/${sessionId}/narration`, 'POST', { revision, request_id: pending.current })
      if (current === generation.current) { setJob(result.narration); pending.current = '' }
    } catch (cause) { if (current === generation.current) setError(String(cause)) } finally { if (current === generation.current) setBusy(false) }
  }
  const cancel = async () => {
    if (!job) return
    const current = generation.current
    try {
      const result = await requestJson<{ narration: Job }>(`${baseUrl}/narrations/${job.id}/cancel`, 'POST', {})
      if (current === generation.current) setJob(result.narration)
    } catch (cause) { if (current === generation.current) setError(String(cause)) }
  }
  const active = !!job && ['queued', 'running'].includes(job.status)
  return <section aria-label="Story narration" className="space-y-2">
    <Button disabled={busy || active} onClick={() => void start()}>Narrate this scene</Button>
    {active && <Button onClick={() => void cancel()}>Cancel narration</Button>}
    {job && <p role="status">Narration: {job.status}{job.error ? ` — ${job.error}` : ''}</p>}
    {error && <p role="alert">{error}</p>}
    {audio && <OwnedAudio src={audio} baseUrl={baseUrl} />}
  </section>
}
