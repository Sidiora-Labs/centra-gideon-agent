import { useEffect, useState, useSyncExternalStore } from 'react'
import { MicCaptureSession, type HandsFreeOptions } from './micCaptureSession'

export { HANDS_FREE_SEGMENT_MS, type MicState, type HandsFreeOptions } from './micCaptureSession'

export function useMicRecorder(
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>,
  onText?: (text: string) => void,
  onError?: (msg: string) => void,
  handsFree?: HandsFreeOptions,
) {
  const [capture] = useState(() => new MicCaptureSession())
  capture.callbacks = { onTranscribe, onText, onError, handsFree }
  const status = useSyncExternalStore(capture.subscribe, capture.getSnapshot, capture.getSnapshot)
  const muted = !!handsFree?.enabled && !!handsFree.muted
  useEffect(() => { capture.activate(); return capture.dispose }, [capture])
  useEffect(capture.configure, [capture, handsFree?.enabled, muted])
  return { ...status, toggle: capture.toggle, drain: capture.drain, muted }
}
