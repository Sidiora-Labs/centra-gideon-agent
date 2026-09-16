import { CUES, CUE_POINTS, playCue, type CueName, type CuePoint } from '../../shared/theme/soundCues'
import { PUSH_CUE_MESSAGE } from './pushPolicy'

const anchors = new Set<string>(CUE_POINTS)
export function pushCue(data: unknown): { point: CuePoint; voice: CueName } | undefined {
  if (!data || typeof data !== 'object') return
  const message = data as { type?: unknown; cue?: unknown }
  if (message.type !== PUSH_CUE_MESSAGE || typeof message.cue !== 'string' || !Object.hasOwn(CUES, message.cue)) return
  return { point: anchors.has(message.cue) ? message.cue as CuePoint : 'turn_complete', voice: message.cue as CueName }
}
export function installPushCuePlayback(): () => void {
  const worker = typeof navigator === 'undefined' ? undefined : navigator.serviceWorker
  if (!worker) return () => {}
  const receive = ({ data }: MessageEvent) => {
    const cue = pushCue(data)
    if (cue) playCue(cue.point, cue.voice)
  }
  worker.addEventListener('message', receive)
  return () => worker.removeEventListener('message', receive)
}
