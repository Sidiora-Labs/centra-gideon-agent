import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import {
  acquireDisplayStream,
  chooseCaptureProvider,
  cropToPngFile,
  cropViewStyle,
  displayCaptureSupported,
  grabOneFrame,
} from './displayCapture'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the app has exactly one getDisplayMedia call site', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  it('only displayCapture.ts invokes it', () => {
    const callers = files.filter((f) => /getDisplayMedia\s*\(/.test(f.src)).map((f) => f.rel).sort()
    expect(
      callers,
      'A second display-capture acquisition forks the teardown. Call acquireDisplayStream/grabOneFrame ' +
        'from ui/composer/displayCapture.ts instead.',
    ).toEqual(['shared/ui/composer/displayCapture.ts'])
  })

  it('and invokes it once', () => {
    const src = files.find((f) => f.rel === 'shared/ui/composer/displayCapture.ts')!.src
    expect(src.match(/getDisplayMedia\s*\(/g)?.length).toBe(1)
  })

  it('the census is not vacuous — it can see a caller at all', () => {
    expect(files.some((f) => /getDisplayMedia/.test(f.src))).toBe(true)
    expect(files.length).toBeGreaterThan(100)
  })

  it('screen sharing consumes the shared acquisition and teardown', () => {
    const share = files.find((f) => f.rel === 'shared/ui/composer/useScreenShare.ts')!.src
    expect(share).toMatch(/acquireDisplayStream/)
    expect(share).toMatch(/stopStream\(stream\)/)
  })
})


describe('chooseCaptureProvider', () => {
  it('keeps the native OS snip on macOS', () => {
    expect(chooseCaptureProvider('darwin', true)).toBe('native')
    expect(chooseCaptureProvider('darwin', false)).toBe('native')
  })

  it('falls back to the browser path when the native snip could not run', () => {
    expect(chooseCaptureProvider('darwin', true, true)).toBe('browser')
    expect(chooseCaptureProvider('darwin', false, true)).toBe('none')
  })

  it('uses the browser path off macOS', () => {
    expect(chooseCaptureProvider('linux', true)).toBe('browser')
    expect(chooseCaptureProvider('win32', true)).toBe('browser')
  })

  it('hides the entry where no display capture exists (iOS Safari)', () => {
    expect(chooseCaptureProvider('linux', false)).toBe('none')
    expect(chooseCaptureProvider('', false)).toBe('none')
  })

  it('routes an unresolved platform to the browser path', () => {
    expect(chooseCaptureProvider('', true)).toBe('browser')
  })
})


interface FakeTrack { stop: () => void; stopped: number }

function fakeStream(trackCount = 2) {
  const tracks: FakeTrack[] = []
  for (let i = 0; i < trackCount; i++) {
    const t: FakeTrack = { stopped: 0, stop: () => { t.stopped += 1 } }
    tracks.push(t)
  }
  const stream = {
    getTracks: () => tracks,
    getVideoTracks: () => tracks,
  } as unknown as MediaStream
  return { stream, tracks }
}

const ORIGINAL = {
  mediaDevices: navigator.mediaDevices,
  getContext: HTMLCanvasElement.prototype.getContext,
  toBlob: HTMLCanvasElement.prototype.toBlob,
  toDataURL: HTMLCanvasElement.prototype.toDataURL,
  videoWidth: Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype, 'videoWidth'),
  videoHeight: Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype, 'videoHeight'),
  play: HTMLVideoElement.prototype.play,
}

function installFakes(opts: { frameSize?: [number, number] } = {}) {
  const [w, h] = opts.frameSize ?? [1920, 1080]
  const drawCalls: unknown[][] = []
  Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', { configurable: true, get: () => w })
  Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', { configurable: true, get: () => h })
  HTMLVideoElement.prototype.play = (() => Promise.resolve()) as never
  HTMLCanvasElement.prototype.getContext = (() => ({
    drawImage: (...args: unknown[]) => { drawCalls.push(args) },
  })) as never
  HTMLCanvasElement.prototype.toDataURL = (() => 'data:image/png;base64,AAAA') as never
  HTMLCanvasElement.prototype.toBlob = ((cb: BlobCallback) => cb(new Blob(['png'], { type: 'image/png' }))) as never
  return { drawCalls }
}

function installDisplayMedia(impl: () => Promise<MediaStream>) {
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true, writable: true,
    value: { getDisplayMedia: impl } as unknown as MediaDevices,
  })
}

let fakes = installFakes()

beforeEach(() => { fakes = installFakes() })

afterEach(() => {
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, writable: true, value: ORIGINAL.mediaDevices })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  HTMLCanvasElement.prototype.toBlob = ORIGINAL.toBlob
  HTMLCanvasElement.prototype.toDataURL = ORIGINAL.toDataURL
  HTMLVideoElement.prototype.play = ORIGINAL.play
  if (ORIGINAL.videoWidth) Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', ORIGINAL.videoWidth)
  if (ORIGINAL.videoHeight) Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', ORIGINAL.videoHeight)
})

describe('grabOneFrame — one frame, then nothing', () => {
  it('stops EVERY track as soon as the frame is read', async () => {
    const { stream, tracks } = fakeStream(3)
    installDisplayMedia(() => Promise.resolve(stream))
    const r = await grabOneFrame()
    expect('frame' in r, 'the fakes should have produced a frame').toBe(true)
    expect(tracks.map((t) => t.stopped)).toEqual([1, 1, 1])
  })

  it('returns the frame at native resolution — no downscale before OCR', async () => {
    const { stream } = fakeStream(1)
    installDisplayMedia(() => Promise.resolve(stream))
    installFakes({ frameSize: [3840, 2160] })
    const r = await grabOneFrame()
    expect('frame' in r && r.frame.width).toBe(3840)
    expect('frame' in r && r.frame.height).toBe(2160)
  })

  it('stops the tracks even when no frame could be drawn', async () => {
    const { stream, tracks } = fakeStream(2)
    installDisplayMedia(() => Promise.resolve(stream))
    installFakes({ frameSize: [0, 0] })
    const r = await grabOneFrame()
    expect(r).toEqual({ error: 'failed' })
    expect(tracks.map((t) => t.stopped)).toEqual([1, 1])
  })

  it('never streams: no timer or animation loop anywhere in the module', () => {
    const src = strip(readFileSync(join(SRC, 'shared/ui/composer/displayCapture.ts'), 'utf8'))
    expect(src).not.toMatch(/setInterval|requestAnimationFrame/)
  })

  it('reports a dismissed picker as a cancellation, not a failure', async () => {
    installDisplayMedia(() => Promise.reject(Object.assign(new Error('no'), { name: 'NotAllowedError' })))
    expect(await grabOneFrame()).toEqual({ error: 'cancelled' })
    installDisplayMedia(() => Promise.reject(Object.assign(new Error('no'), { name: 'AbortError' })))
    expect(await acquireDisplayStream()).toBe('cancelled')
  })

  it('reports a real error as a failure', async () => {
    installDisplayMedia(() => Promise.reject(Object.assign(new Error('boom'), { name: 'NotReadableError' })))
    expect(await grabOneFrame()).toEqual({ error: 'failed' })
  })

  it('asks for nothing where the API is absent', async () => {
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, writable: true, value: {} as MediaDevices })
    expect(displayCaptureSupported()).toBe(false)
    expect(await grabOneFrame()).toEqual({ error: 'unsupported' })
  })

  it('asks for video only — audio is a different feature with a different consent story', async () => {
    const { stream } = fakeStream(1)
    const spy = vi.fn(() => Promise.resolve(stream))
    installDisplayMedia(spy)
    await grabOneFrame()
    expect(spy).toHaveBeenCalledWith({ video: true, audio: false })
  })
})

describe('cropViewStyle — the crop preview geometry', () => {
  const view = cropViewStyle({ x: 70, y: 270, width: 760, height: 90 }, 1280, 720)

  it('places the selection box over the frame in both axes', () => {
    expect(view.selection).toEqual({
      left: `${(70 / 1280) * 100}%`,
      top: `${(270 / 720) * 100}%`,
      width: `${(760 / 1280) * 100}%`,
      height: `${(90 / 720) * 100}%`,
    })
  })

  it('scales the clipped copy to the whole frame', () => {
    expect(view.image.width).toBe(`${(1280 / 760) * 100}%`)
    expect(view.image.height).toBe(`${(720 / 90) * 100}%`)
  })

  it('offsets the copy with a transform, never a percentage margin', () => {
    expect(view.image.transform).toBe(`translate(${(-70 / 1280) * 100}%, ${(-270 / 720) * 100}%)`)
    expect(JSON.stringify(view.image)).not.toMatch(/margin/i)
  })

  it('a full-frame selection sits flush with no offset', () => {
    const full = cropViewStyle({ x: 0, y: 0, width: 800, height: 600 }, 800, 600)
    expect(full.selection).toEqual({ left: '0%', top: '0%', width: '100%', height: '100%' })
    expect(full.image).toEqual({ width: '100%', height: '100%', transform: 'translate(0%, 0%)' })
  })

  it('survives a degenerate frame without dividing by zero', () => {
    const bad = cropViewStyle({ x: 0, y: 0, width: 0, height: 0 }, 0, 0)
    expect(Object.values(bad.selection).every((v) => !v.includes('NaN'))).toBe(true)
    expect(bad.image.transform).not.toMatch(/NaN|Infinity/)
  })
})

describe('cropToPngFile', () => {
  it('crops the requested SOURCE rect and encodes a PNG file', async () => {
    const source = document.createElement('canvas')
    const file = await cropToPngFile(source, { x: 120, y: 60, width: 400, height: 220 }, 'snip.png')
    expect(file?.name).toBe('snip.png')
    expect(file?.type).toBe('image/png')
    expect(fakes.drawCalls.at(-1)).toEqual([source, 120, 60, 400, 220, 0, 0, 400, 220])
  })

  it('names an unnamed snip so the attachment chip has something to show', async () => {
    const file = await cropToPngFile(document.createElement('canvas'), { x: 0, y: 0, width: 10, height: 10 })
    expect(file?.name).toMatch(/^screen-snip-\d+\.png$/)
  })

  it('never emits a zero-area crop', async () => {
    const file = await cropToPngFile(document.createElement('canvas'), { x: 0, y: 0, width: 0, height: 0 })
    expect(file).toBeTruthy()
    expect(fakes.drawCalls.at(-1)).toEqual([expect.anything(), 0, 0, 1, 1, 0, 0, 1, 1])
  })
})
