import { useEffect, useRef, useState } from 'react'
import { reportSpeechPlayback } from './speechPlayback'
import { Button } from '../../../shared/ui/Button'
import { claimAudible, releaseAudible, renewAudible, type AudibleLease } from './audibleOwner'
export default function OwnedAudio({ src, baseUrl, label = 'Scene narration audio', lease: supplied, autoPlay = false, onStopped }: { src: string; baseUrl: string; label?: string; lease?: AudibleLease; autoPlay?: boolean; onStopped?: () => void }) {
  const playbackId = useRef(crypto.randomUUID())
  const element = useRef<HTMLAudioElement>(null)
  const held = useRef<AudibleLease | null>(null)
  const generation = useRef(0)
  const deadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState('')
  const stop = () => {
    reportSpeechPlayback(playbackId.current, false)
    generation.current++
    clearTimeout(deadline.current)
    setBusy(false)
    element.current?.pause()
    setPlaying(false)
    const lease = held.current
    held.current = null
    if (lease) onStopped?.()
    if (lease && !supplied) void releaseAudible(baseUrl, lease).catch(() => {})
  }
  const arm = (lease: AudibleLease) => { clearTimeout(deadline.current); deadline.current = setTimeout(() => { stop(); setError('Audible ownership expired.') }, Math.max(0, lease.expires_at * 1000 - Date.now())) }
  useEffect(() => {
    const timer = setInterval(() => {
      const lease = held.current
      if (!lease) return
      if (Date.now() >= lease.expires_at * 1000) { stop(); setError('Audible ownership expired.'); return }
      renewAudible(baseUrl, lease).then(next => { if (held.current === lease) { held.current = next; arm(next) } }).catch(cause => { stop(); setError(String(cause)) })
    }, 3000)
    return () => { clearInterval(timer); stop() }
  }, [src, baseUrl, supplied?.token])
  const play = async () => {
    if (busy) return
    setBusy(true)
    const current = ++generation.current
    setError('')
    try {
      const lease = supplied ? await renewAudible(baseUrl, supplied) : await claimAudible(baseUrl)
      if (current !== generation.current) { if (!supplied) await releaseAudible(baseUrl, lease); return }
      held.current = lease
      arm(lease)
      await element.current!.play()
      if (current === generation.current) { setPlaying(true); setBusy(false) }
    } catch (cause) { stop(); setError(String(cause)) }
  }
  useEffect(() => { if (autoPlay) void play() }, [src, autoPlay])
  return <div><audio ref={element} aria-label={label} src={src} onPlaying={() => { if (held.current && held.current.expires_at * 1000 > Date.now()) reportSpeechPlayback(playbackId.current, true); else stop() }} onPause={() => reportSpeechPlayback(playbackId.current, false)} onEnded={stop} onError={() => { stop(); setError('Audio playback failed.') }} />
    <Button disabled={busy} onClick={() => playing ? stop() : void play()}>{playing ? 'Pause speech' : 'Play speech'}</Button>
    {error && <p role="alert">{error}</p>}
  </div>
}
