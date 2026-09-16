import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Orbit } from 'lucide-react'
import { prefersReducedMotion } from '../../../shared/theme/motion'
import { useAgentActivity, type AgentActivityEntity } from '../../../shared/data/useAgentActivity'
import { SlotEmptyState } from '../widgets/kit'
import {
  KIND_SCALE, STATE_VISUAL, interpolateScene, layoutScene, pickRenderTier, sceneSummary,
  staticScene, type RenderTier, type SceneNode,
} from './worldScene'


const BASE_NODE = 7
const GLOW_LAYERS = 3

function resolveTone(root: Element, tone: string, fallback = ''): string {
  return getComputedStyle(root).getPropertyValue(tone).trim() || fallback
}

function crossfadeAlpha(mix: number): { from: number; to: number } {
  return { from: 1 - mix, to: mix }
}

export function AgentWorld() {
  const { entities, truncated, error, loading } = useAgentActivity()
  const reduced = prefersReducedMotion()

  const [canvasEl, setCanvasEl] = useState<HTMLCanvasElement | null>(null)
  const [tier, setTier] = useState<RenderTier | null>(null)
  const attachCanvas = useCallback((el: HTMLCanvasElement | null) => {
    setCanvasEl(el)
    setTier(el ? pickRenderTier(el) : null)
  }, [])

  const summary = useMemo(() => sceneSummary(entities, truncated), [entities, truncated])

  const nodes = useRef<SceneNode[]>([])
  const target = useMemo(() => layoutScene(entities), [entities])
  const settled = useMemo(() => staticScene(entities), [entities])

  useEffect(() => {
    const canvas = canvasEl
    if (!canvas || tier !== '2d') return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const root = document.documentElement

    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      const w = canvas.clientWidth || 1
      const h = canvas.clientHeight || 1
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      return { w, h }
    }
    let size = resize()
    const onResize = () => { size = resize(); if (reduced) paint(settled, 0, size) }
    window.addEventListener('resize', onResize)

    if (reduced) {
      nodes.current = settled
      paint(settled, 0, size)
      return () => { window.removeEventListener('resize', onResize) }
    }

    let raf = 0
    let last = performance.now()
    const frame = (now: number) => {
      const dt = Math.min(64, now - last)
      last = now
      nodes.current = interpolateScene(nodes.current, target, dt)
      paint(nodes.current, now, size)
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }

    function paint(list: SceneNode[], now: number, dim: { w: number; h: number }) {
      const { w, h } = dim
      ctx!.clearRect(0, 0, w, h)
      const ink = getComputedStyle(canvas!).color
      const unit = Math.min(w, h)
      const nodeR = BASE_NODE * Math.max(0.7, Math.min(1.6, unit / 260))

      const guide = resolveTone(root, '--color-outline-variant')
      if (guide) {
        ctx!.save()
        ctx!.globalAlpha = 0.16
        ctx!.strokeStyle = guide
        ctx!.lineWidth = 1
        for (const state of new Set(list.map((n) => n.state))) {
          ctx!.beginPath()
          ctx!.arc(w / 2, h / 2, STATE_VISUAL[state].ring * 0.5 * unit, 0, Math.PI * 2)
          ctx!.stroke()
        }
        ctx!.restore()
      }

      for (const n of list) {
        const spin = n.speed === 0 ? 0 : (now / 1000) * n.speed * 0.18
        const dx = n.x - 0.5
        const dy = n.y - 0.5
        const cos = Math.cos(spin)
        const sin = Math.sin(spin)
        const px = w / 2 + (dx * cos - dy * sin) * unit
        const py = h / 2 + (dx * sin + dy * cos) * unit
        const breathe = n.pulse === 0 ? 1 : 1 + Math.sin(now / 620 + n.phase) * 0.16 * n.pulse
        const r = nodeR * n.r * breathe
        const enter = Math.max(0.35, n.mix === 1 ? 1 : 0.35 + n.mix * 0.65)
        const { from, to } = crossfadeAlpha(n.mix)

        for (const [tone, alpha] of [[n.fromTone, from], [n.tone, to]] as const) {
          if (alpha <= 0.001) continue
          const color = resolveTone(root, tone, ink)
          if (!color) continue
          ctx!.save()
          ctx!.globalCompositeOperation = 'lighter'
          for (let layer = GLOW_LAYERS; layer >= 1; layer--) {
            ctx!.globalAlpha = alpha * enter * (0.1 + 0.16 / layer)
            ctx!.fillStyle = color
            ctx!.beginPath()
            ctx!.arc(px, py, r * (1 + layer * 0.9), 0, Math.PI * 2)
            ctx!.fill()
          }
          ctx!.restore()

          ctx!.save()
          ctx!.globalAlpha = alpha * enter
          ctx!.fillStyle = color
          ctx!.beginPath()
          ctx!.arc(px, py, r, 0, Math.PI * 2)
          ctx!.fill()
          ctx!.restore()

          if (n.progress !== undefined) {
            ctx!.save()
            ctx!.globalAlpha = alpha * enter * 0.9
            ctx!.strokeStyle = color
            ctx!.lineWidth = Math.max(1.5, r * 0.28)
            ctx!.lineCap = 'round'
            ctx!.beginPath()
            ctx!.arc(px, py, r * 2.1, -Math.PI / 2, -Math.PI / 2 + n.progress * Math.PI * 2)
            ctx!.stroke()
            ctx!.restore()
          }
        }
      }
    }
  }, [canvasEl, tier, reduced, target, settled])

  if (error) {
    return <SlotEmptyState icon={Orbit}>Couldn&rsquo;t read what your agents are doing, so the world is unknown right now.</SlotEmptyState>
  }
  if (entities.length === 0) {
    if (loading) return null
    return <SlotEmptyState icon={Orbit}>Nothing is running. Loops, chats and subagents appear here as they start.</SlotEmptyState>
  }

  return (
    <div className="flex min-w-0 flex-col gap-s">
      {
}
      <div className="relative min-w-0 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-low">
        <canvas
          ref={attachCanvas}
          role="img"
          aria-label={`Agent world. ${summary}`}
          className="block h-[15rem] w-full sm:h-[18rem]"
        />
        {
}
        {tier === 'static' && (
          <ul className="absolute inset-0 flex flex-col gap-xs overflow-y-auto p-m">
            {settled.map((n) => (
              <StaticRow key={n.id} entity={entities.find((e) => e.id === n.id)} />
            ))}
          </ul>
        )}
      </div>
      <p data-type="body-s" className="text-on-surface-low">{summary}</p>
    </div>
  )
}

function StaticRow({ entity }: { entity: AgentActivityEntity | undefined }) {
  if (!entity) return null
  const v = STATE_VISUAL[entity.state]
  return (
    <li className="flex min-w-0 items-center gap-s" data-type="body-s">
      <span
        aria-hidden="true"
        className="shrink-0 rounded-full"
        style={{
          background: `var(${v.tone})`,
          width: `${0.45 + KIND_SCALE[entity.kind] * 0.3}rem`,
          height: `${0.45 + KIND_SCALE[entity.kind] * 0.3}rem`,
        }}
      />
      <span className="truncate text-on-surface-var">{entity.title}</span>
    </li>
  )
}
