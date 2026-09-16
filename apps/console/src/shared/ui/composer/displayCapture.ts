import { frameDimensions } from './mediaOwnership'

export const SHARE_MAX_EDGE = 1568
export type AcquireFailure = 'unsupported' | 'cancelled' | 'failed'
export type CaptureProvider = 'native' | 'browser' | 'none'
export interface SnipRect { x: number; y: number; width: number; height: number }
export type OneFrame = { frame: HTMLCanvasElement } | { error: AcquireFailure }

const displayDevices = () => typeof navigator === 'undefined' ? undefined : navigator.mediaDevices
const pixel = (value: number) => Number.isFinite(value) ? value : 0
const extent = (value: number) => Math.max(1, pixel(value))
const percentage = (value: number, total: number) => `${pixel(value) / extent(total) * 100}%`

export function displayCaptureSupported(): boolean {
  return typeof displayDevices()?.getDisplayMedia === 'function'
}

export function chooseCaptureProvider(platform: string, supported: boolean, nativeFailed = false): CaptureProvider {
  const providers: Array<[CaptureProvider, boolean]> = [
    ['native', platform === 'darwin' && !nativeFailed], ['browser', supported], ['none', true],
  ]
  return providers.find(([, available]) => available)![0]
}

export async function acquireDisplayStream(): Promise<MediaStream | AcquireFailure> {
  const devices = displayDevices()
  if (!devices || typeof devices.getDisplayMedia !== 'function') return 'unsupported'
  try {
    return await devices.getDisplayMedia({ video: true, audio: false })
  } catch (error) {
    const cancelled = new Set(['AbortError', 'NotAllowedError'])
    return cancelled.has((error as Error)?.name) ? 'cancelled' : 'failed'
  }
}

export function stopStream(stream: MediaStream | null | undefined): void {
  for (const track of stream?.getTracks() ?? []) track.stop()
}

export async function frameSource(stream: MediaStream): Promise<HTMLVideoElement> {
  const video = Object.assign(document.createElement('video'), { muted: true, playsInline: true, srcObject: stream })
  try { await video.play() } catch { /* A paused source can still expose its current frame. */ }
  return video
}

function canvasSurface(width: number, height: number) {
  const canvas = Object.assign(document.createElement('canvas'), { width, height })
  const context = canvas.getContext('2d')
  return context ? { canvas, context } : null
}

export function drawFrame(video: HTMLVideoElement, maxEdge = 0): HTMLCanvasElement | null {
  const size = frameDimensions(video.videoWidth, video.videoHeight, maxEdge)
  if (!size) return null
  const surface = canvasSurface(size.width, size.height)
  if (!surface) return null
  surface.context.drawImage(video, 0, 0, size.width, size.height)
  return surface.canvas
}

export async function grabOneFrame(): Promise<OneFrame> {
  const acquired = await acquireDisplayStream()
  if (typeof acquired === 'string') return { error: acquired }
  let video: HTMLVideoElement | undefined
  try {
    video = await frameSource(acquired)
    const frame = drawFrame(video)
    return frame ? { frame } : { error: 'failed' }
  } catch {
    return { error: 'failed' }
  } finally {
    if (video) video.srcObject = null
    stopStream(acquired)
  }
}

export function cropViewStyle(rect: SnipRect, width: number, height: number): {
  selection: { left: string; top: string; width: string; height: string }
  image: { width: string; height: string; transform: string }
} {
  const horizontal = { origin: rect.x, selection: rect.width, frame: extent(width) }
  const vertical = { origin: rect.y, selection: rect.height, frame: extent(height) }
  const offset = [horizontal, vertical].map(axis => percentage(-axis.origin, axis.frame)).join(', ')
  return {
    selection: {
      left: percentage(horizontal.origin, horizontal.frame), top: percentage(vertical.origin, vertical.frame),
      width: percentage(horizontal.selection, horizontal.frame), height: percentage(vertical.selection, vertical.frame),
    },
    image: {
      width: percentage(horizontal.frame, horizontal.selection), height: percentage(vertical.frame, vertical.selection),
      transform: `translate(${offset})`,
    },
  }
}

export async function cropToPngFile(source: HTMLCanvasElement, rect: SnipRect, name?: string): Promise<File | null> {
  const width = extent(Math.round(rect.width))
  const height = extent(Math.round(rect.height))
  const surface = canvasSurface(width, height)
  if (!surface) return null
  surface.context.drawImage(source, Math.round(rect.x), Math.round(rect.y), width, height, 0, 0, width, height)
  const blob = await new Promise<Blob | null>(resolve => {
    try { surface.canvas.toBlob(resolve, 'image/png') }
    catch { resolve(null) }
  })
  return blob && new File([blob], name || `screen-snip-${Date.now()}.png`, { type: 'image/png' })
}
