export type PanelSide = 'left' | 'right' | 'top' | 'bottom'
export interface PanelBounds { min: number; max: number }
export const panelAxes = {
  left: { coordinate: 'clientX', dimension: 'innerWidth', sign: 1, grow: ['ArrowRight', 'ArrowUp'], shrink: ['ArrowLeft', 'ArrowDown'] },
  right: { coordinate: 'clientX', dimension: 'innerWidth', sign: -1, grow: ['ArrowLeft', 'ArrowUp'], shrink: ['ArrowRight', 'ArrowDown'] },
  top: { coordinate: 'clientY', dimension: 'innerHeight', sign: 1, grow: ['ArrowDown', 'ArrowRight'], shrink: ['ArrowUp', 'ArrowLeft'] },
  bottom: { coordinate: 'clientY', dimension: 'innerHeight', sign: -1, grow: ['ArrowUp', 'ArrowRight'], shrink: ['ArrowDown', 'ArrowLeft'] },
} as const

export function boundedSize(size: number, bounds: PanelBounds) {
  return Math.max(bounds.min, Math.min(bounds.max, size))
}

export function fitPanel(size: number, viewport: number, peek: number) {
  return Math.min(size, Math.max(0, viewport - peek))
}

export function keyboardSize(key: string, shift: boolean, size: number, side: PanelSide, bounds: PanelBounds): number | null {
  const axis = panelAxes[side]
  const step = shift ? 48 : 16
  let next: number
  if (key === 'Home') next = bounds.min
  else if (key === 'End') next = bounds.max
  else if ((axis.grow as readonly string[]).includes(key)) next = size + step
  else if ((axis.shrink as readonly string[]).includes(key)) next = size - step
  else return null
  return boundedSize(next, bounds)
}

export function readPanelStorage(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}

export function writePanelStorage(key: string, value: string) {
  try { localStorage.setItem(key, value) } catch { /* Session state remains usable without storage. */ }
}

export function initialPanelSize(key: string, fallback: number, bounds: PanelBounds) {
  const stored = readPanelStorage(key)
  const number = Number(stored)
  return stored !== null && Number.isFinite(number) && number >= bounds.min && number <= bounds.max ? number : fallback
}

export class SettledPanelSize {
  private pending: ReturnType<typeof setTimeout> | undefined
  private latest: number | undefined
  constructor(private readonly key: string) {}

  schedule(size: number) {
    this.latest = size
    clearTimeout(this.pending)
    this.pending = setTimeout(() => this.flush(), 200)
  }

  flush() {
    clearTimeout(this.pending)
    this.pending = undefined
    if (this.latest !== undefined) writePanelStorage(this.key, String(this.latest))
  }
}

const selectionOwners = new Set<object>()
let previousSelection = ''
function holdSelection(owner: object) {
  if (selectionOwners.size === 0) previousSelection = document.body.style.userSelect
  selectionOwners.add(owner)
  document.body.style.userSelect = 'none'
}
function releaseSelection(owner: object) {
  selectionOwners.delete(owner)
  if (selectionOwners.size === 0) document.body.style.userSelect = previousSelection
}

export class PointerResizeSession {
  private finished = false
  private readonly target: HTMLElement | Window
  private readonly coordinate: 'clientX' | 'clientY'
  private readonly sign: number
  private readonly start: number

  constructor(
    private readonly handle: HTMLElement,
    private readonly pointerId: number,
    event: { clientX: number; clientY: number },
    side: PanelSide,
    private readonly initialSize: number,
    private readonly bounds: () => PanelBounds,
    private readonly update: (size: number) => void,
  ) {
    const axis = panelAxes[side]
    this.coordinate = axis.coordinate
    this.sign = axis.sign
    this.start = event[this.coordinate]
    let captured = false
    try {
      handle.setPointerCapture(pointerId)
      captured = true
    } catch { /* Window events keep unsupported capture environments operable. */ }
    this.target = captured ? handle : window
    this.target.addEventListener('pointermove', this.move as EventListener)
    this.target.addEventListener('pointerup', this.end as EventListener)
    this.target.addEventListener('pointercancel', this.end as EventListener)
    handle.addEventListener('lostpointercapture', this.end as EventListener)
    holdSelection(this)
  }

  private move = (event: PointerEvent) => {
    if (this.finished || event.pointerId !== this.pointerId) return
    const next = this.initialSize + (event[this.coordinate] - this.start) * this.sign
    this.update(boundedSize(next, this.bounds()))
  }

  private end = (event: PointerEvent) => {
    if (event.pointerId === this.pointerId) this.stop()
  }

  stop() {
    if (this.finished) return
    this.finished = true
    this.target.removeEventListener('pointermove', this.move as EventListener)
    this.target.removeEventListener('pointerup', this.end as EventListener)
    this.target.removeEventListener('pointercancel', this.end as EventListener)
    this.handle.removeEventListener('lostpointercapture', this.end as EventListener)
    try { this.handle.releasePointerCapture(this.pointerId) } catch { /* Capture may already be released. */ }
    releaseSelection(this)
  }
}
