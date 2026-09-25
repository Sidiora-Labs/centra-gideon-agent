import { useEffect, useRef, useState } from 'react'
import { gatewayRequest, requestJson, responseError } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import OwnedAudio from './OwnedAudio'
import { claimAudible, releaseAudible, renewAudible, type AudibleLease } from './audibleOwner'
type Job = { id: string; status: string; error: string }
export default function ProactiveSpeech({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [active, setActive] = useState(false)
  const [status, setStatus] = useState('Proactive speech is off in this browser.')
  const [error, setError] = useState('')
  const [audio, setAudio] = useState('')
  const lease = useRef<AudibleLease | null>(null)
  const generation = useRef(0)
  const retry = useRef(false)
  const lastAudio = useRef('')
  const playingJob = useRef('')
  const release = () => { const held = lease.current; lease.current = null; if (held) void releaseAudible(baseUrl, held).catch(() => {}) }
  useEffect(() => {
    if (!active) return
    let alive = true, timer: ReturnType<typeof setTimeout>, url = ''
    const poll = async () => {
      try {
        const acquired = lease.current ? await renewAudible(baseUrl, lease.current) : await claimAudible(baseUrl)
        if (!alive) { await releaseAudible(baseUrl, acquired); return }
        lease.current = acquired
        const state = await requestJson<{ owner: { enabled: boolean } }>(baseUrl + '/speech-owner')
        if (!state.owner.enabled) { if (alive) setActive(false); return }
        const result = await requestJson<{ source: { state: string; reason?: string }; narration: Job | null }>(baseUrl + '/proactive-speech' + (retry.current ? '/retry' : ''), 'POST', { owner: lease.current.owner, token: lease.current.token })
        if (!alive) return
        retry.current = false
        const job = result.narration
        setStatus(job ? `Digest speech: ${job.status}${job.error ? ' — ' + job.error : ''}` : result.source.reason || result.source.state)
        if (job?.status === 'ready' && lastAudio.current !== job.id) {
          const response = await gatewayRequest(`${baseUrl}/narrations/${job.id}/audio`)
          if (!response.ok) throw await responseError(response)
          const blob = await response.blob()
          if (alive) { if (url) URL.revokeObjectURL(url); url = URL.createObjectURL(blob); lastAudio.current = job.id; playingJob.current = job.id; setAudio(url) }
        }
        if (!job || (!['queued', 'running'].includes(job.status) && playingJob.current !== job.id)) release()
      } catch (cause) { if (alive) { setError(String(cause)); setActive(false) } }
      if (alive) timer = setTimeout(() => void poll(), 3000)
    }
    void poll()
    return () => {
      alive = false; clearTimeout(timer); generation.current++; setAudio('')
      if (url) URL.revokeObjectURL(url)
      const held = lease.current; lease.current = null
      if (held) void releaseAudible(baseUrl, held).catch(() => {})
    }
  }, [active, baseUrl])
  const enable = async () => {
    const current = ++generation.current
    setError('')
    try {
      await requestJson(baseUrl + '/speech-owner/enable', 'POST', { enabled: true })
      if (current === generation.current) setActive(true)
    } catch (cause) { setError(String(cause)) }
  }
  const disable = async () => {
    generation.current++; setActive(false)
    try { await requestJson(baseUrl + '/speech-owner/enable', 'POST', { enabled: false }); setStatus('Proactive speech is off.') }
    catch (cause) { setError(String(cause)) }
  }
  return <section aria-label="Proactive speech" className="space-y-2">
    <Button onClick={() => active ? void disable() : void enable()}>{active ? 'Disable proactive speech' : 'Enable proactive speech here'}</Button>
    {active && /unavailable|failed|cancelled|interrupted/.test(status) && <Button onClick={() => { retry.current = true }}>Retry digest speech</Button>}
    <p role="status">{status}</p>
    {error && <p role="alert">{error}</p>}
    {audio && lease.current && <OwnedAudio src={audio} baseUrl={baseUrl} lease={lease.current} autoPlay onStopped={() => { playingJob.current = ''; setAudio(''); release() }} label="Proactive digest audio" />}
  </section>
}
