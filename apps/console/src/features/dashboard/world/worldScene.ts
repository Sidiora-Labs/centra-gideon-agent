import type { AgentActivityEntity, AgentActivityKind, AgentActivityState } from '../../../shared/data/useAgentActivity'


export const STATE_VISUAL: Record<AgentActivityState, {
  tone: string
  ring: number
  pulse: number
  speed: number
}> = {
  needs_input: { tone: '--color-info', ring: 0.20, pulse: 1.00, speed: 0.50 },
  waiting_approval: { tone: '--color-warn', ring: 0.32, pulse: 0.80, speed: 0.40 },
  error: { tone: '--color-danger', ring: 0.46, pulse: 0.30, speed: 0.15 },
  working: { tone: '--color-ok', ring: 0.62, pulse: 0.55, speed: 1.00 },
  idle: { tone: '--color-on-surface-low', ring: 0.80, pulse: 0.00, speed: 0.10 },
}

export const KIND_SCALE: Record<AgentActivityKind, number> = {
  loop: 1.0, session: 0.72, subagent: 0.5,
}

const RING_ORDER: AgentActivityState[] = ['idle', 'working', 'error', 'waiting_approval', 'needs_input']

export const EASE_TAU = 320

export interface ScenePlacement {
  id: string
  kind: AgentActivityKind
  state: AgentActivityState
  title: string
  x: number
  y: number
  r: number
  tone: string
  pulse: number
  speed: number
  phase: number
  progress?: number
}

export interface SceneNode extends ScenePlacement {
  fromTone: string
  mix: number
}

function phaseOf(id: string): number {
  let h = 2166136261
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 3600) / 3600 * Math.PI * 2
}

export function layoutScene(entities: AgentActivityEntity[]): ScenePlacement[] {
  const out: ScenePlacement[] = []
  for (const state of RING_ORDER) {
    const inRing = entities.filter((e) => e.state === state)
    if (inRing.length === 0) continue
    const v = STATE_VISUAL[state]
    inRing.forEach((e, i) => {
      const angle = (i / inRing.length) * Math.PI * 2 - Math.PI / 2 + phaseOf(e.id) * 0.12
      out.push({
        id: e.id,
        kind: e.kind,
        state: e.state,
        title: e.title,
        x: 0.5 + Math.cos(angle) * v.ring * 0.5,
        y: 0.5 + Math.sin(angle) * v.ring * 0.5,
        r: KIND_SCALE[e.kind],
        tone: v.tone,
        pulse: v.pulse,
        speed: v.speed,
        phase: phaseOf(e.id),
        ...(e.progress === undefined ? {} : { progress: e.progress }),
      })
    })
  }
  return out
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t

export function easeStep(dt: number): number {
  return 1 - Math.exp(-Math.max(0, dt) / EASE_TAU)
}

export function interpolateScene(prev: SceneNode[], target: ScenePlacement[], dt: number): SceneNode[] {
  const t = easeStep(dt)
  const byId = new Map(prev.map((n) => [n.id, n]))
  return target.map((p) => {
    const was = byId.get(p.id)
    if (!was) {
      const dx = p.x - 0.5
      const dy = p.y - 0.5
      return { ...p, x: 0.5 + dx * 1.35, y: 0.5 + dy * 1.35, r: p.r * 0.4, fromTone: p.tone, mix: 0 }
    }
    const changed = was.tone !== p.tone
    return {
      ...p,
      x: lerp(was.x, p.x, t),
      y: lerp(was.y, p.y, t),
      r: lerp(was.r, p.r, t),
      pulse: lerp(was.pulse, p.pulse, t),
      speed: lerp(was.speed, p.speed, t),
      fromTone: changed ? was.tone : was.fromTone,
      mix: changed ? 0 : Math.min(1, lerp(was.mix, 1, t)),
    }
  })
}

export function staticScene(entities: AgentActivityEntity[]): SceneNode[] {
  return layoutScene(entities).map((p) => ({ ...p, pulse: 0, speed: 0, fromTone: p.tone, mix: 1 }))
}

export function sceneSummary(entities: AgentActivityEntity[], truncated: number): string {
  if (entities.length === 0) return 'Nothing is running.'
  const n = (s: AgentActivityState) => entities.filter((e) => e.state === s).length
  const parts: string[] = []
  const say = (count: number, word: string) => { if (count > 0) parts.push(`${count} ${word}`) }
  say(n('needs_input'), 'waiting on you')
  say(n('waiting_approval'), 'waiting for approval')
  say(n('working'), 'working')
  say(n('error'), 'in error')
  say(n('idle'), 'idle')
  const tail = truncated > 0 ? `, and ${truncated} more not shown` : ''
  return `${parts.join(', ')}${tail}.`
}

export type RenderTier = '2d' | 'static'

export function pickRenderTier(canvas: {
  getContext: (id: string, opts?: unknown) => unknown | null
} | null): RenderTier {
  if (!canvas) return 'static'
  try { return canvas.getContext('2d') ? '2d' : 'static' } catch { return 'static' }
}
