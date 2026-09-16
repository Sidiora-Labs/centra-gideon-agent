import { runtime } from '../theme/runtime'
import { GlowTravel, glowField, glowMode, localGlowRect, paintGlowDot, type GlowRect, type GlowStyle } from './glowScene'

export interface GlowSources { composer: HTMLElement | null; focus: HTMLElement | null; intensity: number }

function placeBloom(element: HTMLElement | null, rect: GlowRect | null, opacity: number) {
  if (!element) return
  element.style.opacity = rect ? String(opacity) : '0'
  if (!rect) return
  Object.assign(element.style, {
    left: `${rect.cx - rect.halfW}px`, top: `${rect.cy - rect.halfH}px`,
    width: `${rect.halfW * 2}px`, height: `${rect.halfH * 2}px`,
    ...(rect.radius === undefined ? {} : { borderRadius: `${rect.radius}px` }),
  })
}

export function connectGlowCanvas(canvas: HTMLCanvasElement, blooms: [HTMLElement | null, HTMLElement | null], style: GlowStyle, sources: () => GlowSources) {
  const context = canvas.getContext('2d')
  const parent = canvas.parentElement
  if (!context || !parent) return () => {}
  if (style === 'none') {
    context.setTransform(1, 0, 0, 1, 0, 0)
    context.clearRect(0, 0, canvas.width, canvas.height)
    blooms.forEach(element => placeBloom(element, null, 0))
    return () => {}
  }
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
  const travel = new GlowTravel(sources().intensity)
  let frame: number | undefined
  let active = true
  let width = 0
  let height = 0
  const measure = (element: HTMLElement | null, origin: DOMRect, zoom: number) => {
    if (!element) return null
    const rounded = element.querySelector<HTMLElement>('[style*="border-radius"]') ?? element
    const radius = parseFloat(getComputedStyle(rounded).borderTopLeftRadius) || 0
    return localGlowRect(element.getBoundingClientRect(), origin, zoom, radius)
  }
  const schedule = () => { if (active && frame === undefined) frame = requestAnimationFrame(paint) }
  function paint(time: number) {
    frame = undefined
    if (!active) return
    const mode = glowMode(style, reduced.matches)
    const source = sources()
    const origin = canvas.getBoundingClientRect()
    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1
    const lights = travel.advance(measure(source.composer, origin, zoom), measure(source.focus, origin, zoom), source.intensity)
    placeBloom(blooms[0], lights.primary, 1)
    placeBloom(blooms[1], lights.traveling, Math.min(1, lights.travel * 1.2))
    context!.clearRect(0, 0, width, height)
    if (mode.dots) for (const dot of glowField(width, height, time, runtime, lights)) paintGlowDot(context!, runtime.dotShape, dot)
    if (mode.animate) schedule()
  }
  const resize = () => {
    width = parent.clientWidth; height = parent.clientHeight
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.floor(width * ratio); canvas.height = Math.floor(height * ratio)
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    schedule()
  }
  const motionChanged = () => {
    if (frame !== undefined) cancelAnimationFrame(frame)
    frame = undefined
    schedule()
  }
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
  resize()
  observer?.observe(parent)
  reduced.addEventListener?.('change', motionChanged)
  return () => {
    active = false
    if (frame !== undefined) cancelAnimationFrame(frame)
    observer?.disconnect()
    reduced.removeEventListener?.('change', motionChanged)
  }
}
