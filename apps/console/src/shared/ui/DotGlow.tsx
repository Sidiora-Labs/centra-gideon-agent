import { useEffect, useRef, type RefObject } from 'react'
import { useAppearance } from '../../app/shell/appearance'
import { TOKENS } from '../theme/tokenRegistry'
import { connectGlowCanvas } from './glowCanvas'
import type { GlowStyle } from './glowScene'
import { cx } from './cx'

export type { GlowRect } from './glowScene'
const backdrop = TOKENS.find(token => token.kind === 'select' && token.varName === '--bg-style')
const blooms = [
  '0 0 120px 60px color-mix(in srgb, var(--glow-a) 18%, transparent)',
  '0 0 90px 36px color-mix(in srgb, var(--glow-a) 11%, transparent)',
]
export function DotGlow({ className, intensity = 1, composerRef, focusRef }: {
  className?: string; intensity?: number; composerRef?: RefObject<HTMLElement | null>; focusRef?: RefObject<HTMLElement | null>
}) {
  const appearance = useAppearance()
  const style = (backdrop ? appearance.selectValue(backdrop) : 'waves') as GlowStyle
  const canvas = useRef<HTMLCanvasElement>(null)
  const lights = useRef<[HTMLDivElement | null, HTMLDivElement | null]>([null, null])
  const source = useRef({ composerRef, focusRef, intensity })
  source.current = { composerRef, focusRef, intensity }
  useEffect(() => {
    if (!canvas.current) return
    return connectGlowCanvas(canvas.current, lights.current, style, () => ({
      composer: source.current.composerRef?.current ?? null,
      focus: source.current.focusRef?.current ?? null,
      intensity: source.current.intensity,
    }))
  }, [style, composerRef])
  return <div className={cx('pointer-events-none absolute inset-0 overflow-hidden', className)} aria-hidden>
    {blooms.map((shadow, index) => <div key={shadow} ref={element => { lights.current[index] = element }} className="absolute"
      style={{ borderRadius: 'var(--radius-xli)', boxShadow: shadow, opacity: 0 }} />)}
    <canvas ref={canvas} className="absolute inset-0" />
  </div>
}
