import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../data/api'
import { SHARE_MAX_EDGE, acquireDisplayStream, drawFrame, frameSource, stopStream } from './displayCapture'
import { MediaOwnership, MediaWriteQueue } from './mediaOwnership'

export interface ScreenShareState {
  available: boolean
  sharing: boolean
  disabledReason: string
  toggle: () => void
  captureAndStage: (session: string) => Promise<void>
  lastFrame: () => string
}

interface SharedCapture {
  ticket: number
  session: string
  targets: Set<string>
  stream: MediaStream
  video: HTMLVideoElement | null
  ended: () => void
}

const unavailable = { available: false, disabledReason: '' }
const switchedOff = 'Screen sharing is off. Turn it on in Settings → Chat.'

export function useScreenShare(session: string, onError?: (msg: string) => void): ScreenShareState {
  const [availability, setAvailability] = useState(unavailable)
  const [sharing, setSharing] = useState(false)
  const [ownership] = useState(() => new MediaOwnership())
  const [requests] = useState(() => new MediaOwnership())
  const capture = useRef<SharedCapture | null>(null)
  const pinned = useRef('')
  const active = useRef(false)
  const options = useRef({ session, onError })
  options.current = { session, onError }
  const [writes] = useState(() => new MediaWriteQueue())
  const write = writes.run

  const refresh = useCallback(async () => {
    const ticket = requests.begin()
    try {
      const result = await api.screenShareState(options.current.session)
      if (!requests.finish(ticket) || !active.current) return
      const available = !!result.enabled
      setAvailability({ available, disabledReason: available && result.delivery === 'none' ? result.reason || '' : '' })
    } catch {
      if (requests.finish(ticket) && active.current) setAvailability(unavailable)
    }
  }, [requests])

  const teardown = useCallback((notify: boolean) => {
    ownership.cancel()
    const owned = capture.current
    capture.current = null
    pinned.current = ''
    if (owned) {
      const { stream, video, ended } = owned
      stream.getVideoTracks().forEach(track => track.removeEventListener('ended', ended))
      if (video) video.srcObject = null
      stopStream(stream)
      if (notify) {
        for (const target of new Set([owned.session, ...owned.targets])) {
          if (target) void write(() => api.screenShareSignal(target, 'stop')).catch(() => {})
        }
      }
    }
    if (active.current) setSharing(false)
  }, [ownership, write])

  useEffect(() => {
    active.current = true
    setSharing(false)
    return () => {
      active.current = false
      requests.cancel()
      teardown(true)
    }
  }, [requests, teardown])

  useEffect(() => {
    void refresh()
    return () => requests.cancel()
  }, [session, refresh, requests])

  const start = useCallback(async () => {
    if (!active.current || ownership.busy) return
    const ticket = ownership.begin()
    const acquired = await acquireDisplayStream()
    if (!ownership.owns(ticket)) {
      if (typeof acquired !== 'string') stopStream(acquired)
      return
    }
    if (typeof acquired === 'string') {
      ownership.finish(ticket)
      if (acquired !== 'cancelled') options.current.onError?.(acquired === 'unsupported'
        ? 'This browser cannot share a screen.' : 'Screen sharing could not start.')
      return
    }
    const ownerSession = options.current.session
    const owned: SharedCapture = {
      ticket, session: ownerSession, targets: new Set(), stream: acquired, video: null,
      ended: () => { if (ownership.owns(ticket)) teardown(true) },
    }
    capture.current = owned
    acquired.getVideoTracks().forEach(track => track.addEventListener('ended', owned.ended))
    try {
      await write(() => api.screenShareSignal(ownerSession, 'start'))
    } catch {
      if (!ownership.owns(ticket)) return
      teardown(false)
      options.current.onError?.(switchedOff)
      void refresh()
      return
    }
    if (!ownership.owns(ticket)) return
    try {
      const video = await frameSource(acquired)
      if (!ownership.owns(ticket)) { video.srcObject = null; return }
      owned.video = video
      if (acquired.getVideoTracks().some(track => track.readyState === 'ended')) teardown(true)
      else setSharing(true)
    } catch {
      if (!ownership.owns(ticket)) return
      teardown(true)
      options.current.onError?.('Screen sharing could not start.')
    }
  }, [ownership, refresh, teardown, write])

  const toggle = useCallback(() => {
    if (ownership.busy) teardown(true)
    else void start()
  }, [ownership, start, teardown])

  const captureAndStage = useCallback(async (target: string) => {
    const owned = capture.current
    if (!owned?.video || !target || !ownership.owns(owned.ticket)) return
    if (owned.stream.getVideoTracks()[0]?.readyState !== 'live') return
    let frame: string
    try {
      const canvas = drawFrame(owned.video, SHARE_MAX_EDGE)
      if (!canvas) return
      frame = canvas.toDataURL('image/jpeg', 0.72)
      if (!frame) return
    } catch { return }
    pinned.current = frame
    owned.targets.add(target)
    try {
      await write(() => api.stageScreenFrame(target, frame))
    } catch {
      if (!ownership.owns(owned.ticket)) return
      teardown(false)
      void refresh()
      options.current.onError?.(switchedOff)
    }
  }, [ownership, refresh, teardown, write])

  const lastFrame = useCallback(() => pinned.current, [])
  return { ...availability, sharing, toggle, captureAndStage, lastFrame }
}
