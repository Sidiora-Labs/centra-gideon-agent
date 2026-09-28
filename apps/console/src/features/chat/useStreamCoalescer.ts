import { useCallback, useEffect, useRef } from 'react'
import { runtime } from '../../shared/theme/runtime'
import { prefersReducedMotion } from '../../shared/theme/motion'


export const FRAME_MS = 16
export const MIN_BUDGET = 2
export const MAX_BUDGET = 400
export const MAX_LAG = 1200
const EMA_ALPHA = 0.3

export type StreamCursor = Readonly<{
  stream_epoch: string
  stream_turn: number
  stream_seq: number
}>

export class CoalescerCore {
  private pending = ''
  private revealed = 0
  private emitted = 0
  private ema = 0
  private drain = 1
  private cursor: StreamCursor | null = null

  push(chunk: string, cursor?: StreamCursor): boolean {
    if (cursor && this.cursor && cursor.stream_epoch === this.cursor.stream_epoch) {
      if (cursor.stream_turn < this.cursor.stream_turn) return false
      if (cursor.stream_turn === this.cursor.stream_turn && cursor.stream_seq <= this.cursor.stream_seq) return false
    }
    this.pending += chunk
    if (cursor) this.cursor = cursor
    return true
  }

  resume(partial: string | null, cursor: StreamCursor | null): void {
    this.pending = partial ?? ''
    this.revealed = this.pending.length
    this.emitted = this.revealed
    this.cursor = cursor
    this.ema = 0
    this.drain = 1
  }

  backlog(): number { return this.pending.length - this.revealed }

  revealedText(): string { return this.pending.slice(0, this.revealed) }

  /** Consume newly revealed progress once within this text run. */
  takeRevealed(): string | null {
    if (this.revealed <= this.emitted) return null
    this.emitted = this.revealed
    return this.revealedText()
  }

  drainAll(): string { this.revealed = this.pending.length; return this.pending }

  reset(): void { this.pending = ''; this.revealed = 0; this.emitted = 0; this.ema = 0; this.drain = 1 }

  clear(): void { this.reset(); this.cursor = null }

  tick(speed: number): string {
    const backlog = this.backlog()
    if (backlog <= 0) return this.revealedText()
    const s = Math.max(0.1, speed)
    this.drain = backlog > MAX_LAG ? Math.min(8, this.drain + 1) : Math.max(1, this.drain - 0.25)
    this.ema = EMA_ALPHA * backlog + (1 - EMA_ALPHA) * this.ema
    const budget = Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Math.ceil(this.ema * this.drain * s)))
    const from = this.revealed
    let to = Math.min(this.pending.length, from + budget)
    if (backlog <= MAX_LAG && to < this.pending.length && !this._isBoundary(this.pending[to])) {
      const snapped = this._lastBoundary(from + 1, to)
      if (snapped > from) to = snapped
    }
    this.revealed = to
    return this.revealedText()
  }

  private _isBoundary(ch: string): boolean {
    if (ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r') return true
    const c = ch.codePointAt(0) ?? 0
    return (c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3040 && c <= 0x30ff)
  }

  private _lastBoundary(lo: number, hi: number): number {
    for (let i = hi; i >= lo; i--) {
      if (this._isBoundary(this.pending[i])) return i
      if (i > 0 && this._isBoundary(this.pending[i - 1])) return i
    }
    return -1
  }
}

export interface StreamCoalescer {
  push: (chunk: string, cursor?: StreamCursor) => boolean
  resume: (partial: string | null, cursor: StreamCursor | null) => void
  /** Drain the backlog and emit only new progress. Safe across consecutive boundaries. */
  flushNow: () => void
  reset: () => void
  clear: () => void
}

export function useStreamCoalescer(
  onFlush: (revealedSoFar: string) => void,
  opts: { immediate?: boolean } = {},
): StreamCoalescer {
  const onFlushRef = useRef(onFlush); onFlushRef.current = onFlush
  const immediateRef = useRef(opts.immediate); immediateRef.current = opts.immediate

  const coreRef = useRef<CoalescerCore | null>(null)
  if (!coreRef.current) coreRef.current = new CoalescerCore()
  const rafRef = useRef(0)
  const lastTsRef = useRef(0)

  const isImmediate = () =>
    immediateRef.current === true
    || runtime.animSpeed === 0
    || prefersReducedMotion()

  const stop = () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0 } }

  const frame = useCallback((ts: number) => {
    rafRef.current = 0
    if (!lastTsRef.current) lastTsRef.current = ts
    if (ts - lastTsRef.current < FRAME_MS) { rafRef.current = requestAnimationFrame(frame); return }
    lastTsRef.current = ts
    const core = coreRef.current!
    core.tick(runtime.animSpeed)
    const update = core.takeRevealed()
    if (update !== null) onFlushRef.current(update)
    if (core.backlog() > 0) rafRef.current = requestAnimationFrame(frame)
  }, [])

  const flushNow = useCallback(() => {
    stop(); lastTsRef.current = 0
    const core = coreRef.current!
    core.drainAll()
    const update = core.takeRevealed()
    if (update !== null) onFlushRef.current(update)
  }, [])

  const reset = useCallback(() => { stop(); lastTsRef.current = 0; coreRef.current!.reset() }, [])
  const resume = useCallback((partial: string | null, cursor: StreamCursor | null) => {
    stop(); lastTsRef.current = 0; coreRef.current!.resume(partial, cursor)
  }, [])
  const clear = useCallback(() => { stop(); lastTsRef.current = 0; coreRef.current!.clear() }, [])

  const push = useCallback((chunk: string, cursor?: StreamCursor) => {
    if (!coreRef.current!.push(chunk, cursor)) return false
    if (isImmediate()) { flushNow(); return true }
    if (!rafRef.current) rafRef.current = requestAnimationFrame(frame)
    return true
  }, [frame, flushNow])

  useEffect(() => () => stop(), [])

  return { push, resume, flushNow, reset, clear }
}
