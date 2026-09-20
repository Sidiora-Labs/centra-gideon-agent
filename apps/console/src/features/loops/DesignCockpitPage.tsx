import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { fvs, withWeight } from '../../shared/theme/fontWeight'
import {
  ArrowLeft, Pause, Play, Square, MessageSquarePlus, MessageSquare, Trash2, Link2, Check,
  Palette, Type, Ruler, Box, Download, Upload, FolderKanban, Layers, RefreshCw, Contrast, GripVertical, Repeat,
  CheckCircle2, CircleDot, Circle, Clock,
} from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { ReactWidgetFrame } from '../../shared/ui/widget/ReactWidgetFrame'
import { api, type Loop, type Artifact, type LoopPhase } from '../../shared/data/api'
import { downloadText, safeFilename } from '../../shared/data/download'
import { shownCycle, effectiveLoopStatus, ACTIVE_LOOP_STATUSES, PRELAUNCH_LOOP_STATUSES, LOOP_ACTION_SOURCE_STATUSES, type LoopAction } from '../../shared/data/loopStatus'
import { useRunStream } from './useRunStream'
import { CockpitPromptBar } from './CockpitPromptBar'
import type { RouteProps } from '../../app/shell/useQueryState'
import { promptInput } from '../../shared/ui/dialog'
import { accentChip } from '../../shared/theme/accent'
import { notify } from '../../app/shell/appSdk'
import { copyText } from '../../app/shell/clipboard'
import { MoreRow } from '../../shared/ui/MoreRow'

export type Scheme = 'light' | 'dark'
type Tab = 'tokens' | 'canvas' | 'palette' | 'contrast' | 'exports'
type PaletteScale = 'brand' | 'accent' | 'neutral'
const PALETTE_SCALES: { scale: PaletteScale; label: string }[] = [
  { scale: 'brand', label: 'Set brand' },
  { scale: 'accent', label: 'Set accent' },
  { scale: 'neutral', label: 'Set neutral' },
]

export interface ResolvedTokens {
  resolved: Record<string, any>
  css: string
  overrides: Record<string, any>
  scheme: string
}


export function DesignCockpitPage({ id, onBack, onDeleted, onOpenProject, onBuildWithChat, query, setQuery }: {
  id: string; onBack: () => void; onDeleted?: () => void; onOpenProject?: (projectId: string) => void
  onBuildWithChat?: (loop: Loop) => void
} & Partial<Pick<RouteProps, 'query' | 'setQuery'>>) {
  const [loop, setLoop] = useState<Loop | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [scheme, setScheme] = useState<Scheme>('light')
  const tab = ((query?.dtab as Tab) || 'tokens') as Tab
  const setTab = (t: Tab) => setQuery?.({ dtab: t === 'tokens' ? null : t })
  const [tokens, setTokens] = useState<ResolvedTokens | null>(null)
  const [tokensErr, setTokensErr] = useState<unknown>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [linkCopied, setLinkCopied] = useState(false)
  const [confirmStop, setConfirmStop] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [nudgeOpen, setNudgeOpen] = useState(false)
  const [nudgeText, setNudgeText] = useState('')
  const [projName, setProjName] = useState('')

  const loadLoop = useCallback(() => {
    api.uLoop(id).then((l) => {
      setLoop(l); setNotFound(false)
      const pid = l.project_id || l.tasks_project_id || ''
      if (pid) api.project(pid).then((p) => setProjName(p?.name || '')).catch(() => {})
    }).catch((e) => { if (e?.status === 404) setNotFound(true) })
  }, [id])

  const loadTokens = useCallback(() => {
    setTokensErr(null)
    api.uLoopDesignTokens(id, scheme)
      .then((t) => setTokens(t as ResolvedTokens))
      .catch((e) => setTokensErr(e ?? new Error('tokens unavailable')))
  }, [id, scheme])

  const loadArtifacts = useCallback(() => {
    api.artifacts({ tag: `loop:${id}` }).then(setArtifacts).catch(() => {})
  }, [id])

  useEffect(() => { loadLoop() }, [loadLoop])
  useEffect(() => { loadTokens() }, [loadTokens])
  useEffect(() => { loadArtifacts() }, [loadArtifacts])

  useEffect(() => {
    if (!confirmDelete) return
    const t = window.setTimeout(() => setConfirmDelete(false), 4000)
    return () => window.clearTimeout(t)
  }, [confirmDelete])

  const { connected } = useRunStream(id, !notFound, {
    onSnapshot: (l) => setLoop(l),
    onLifecycle: () => { loadLoop(); loadTokens(); loadArtifacts() },
  })

  const status = loop?.status
  const active = !!status && ACTIVE_LOOP_STATUSES.has(status)
  const running = status === 'running'
  const canAct = (action: LoopAction) => !!status && LOOP_ACTION_SOURCE_STATUSES[action].has(status)
  const specFrozen = !!status && !PRELAUNCH_LOOP_STATUSES.has(status)

  async function act(a: LoopAction) {
    if (!(await reportingWrite(`${a} this loop`, () => api.uLoopAction(id, a)))) return
    loadLoop()
  }
  async function sendNudge() {
    const t = nudgeText.trim(); if (!t) return
    if (!(await reportingWrite('send that nudge', () => api.uLoopNudge(id, t)))) return
    setNudgeText(''); setNudgeOpen(false)
  }
  async function del() {
    if (!confirmDelete) { setConfirmDelete(true); return }
    try { await api.deleteULoop(id) }
    catch (e) { notify(`Couldn't delete this loop: ${String((e as Error)?.message || e)}`, 'error'); return }
    onDeleted?.()
  }
  function copyLink() {
    void copyText(`${location.origin}/#/loops/${id}`, 'the link').then((ok) => {
      if (ok) { setLinkCopied(true); setTimeout(() => setLinkCopied(false), 1500) }
    })
  }

  const applyColorOverride = useCallback(async (scale: PaletteScale, hex: string) => {
    if (!loop) return
    const kc = (loop.kind_config || {}) as Record<string, any>
    const prev = (kc.token_overrides || {}) as Record<string, any>
    const next = {
      ...prev,
      color: { ...(prev.color || {}), primitive: {
        ...((prev.color || {}).primitive || {}),
        [scale]: buildRamp(hex),
      } },
    }
    if (!(await reportingWrite(`apply the ${scale} colour`,
      () => api.updateULoop(id, { kind_config: { ...kc, token_overrides: next } })))) return
    loadLoop(); loadTokens()
  }, [loop, id, loadLoop, loadTokens])

  const setTokenOverride = useCallback(async (path: string, value: string) => {
    if (!loop) return
    const kc = (loop.kind_config || {}) as Record<string, any>
    const ov = JSON.parse(JSON.stringify(kc.token_overrides || {}))
    const segs = path.split('.')
    const chain: any[] = [ov]
    let node = ov
    for (let i = 0; i < segs.length - 1; i++) { node[segs[i]] = node[segs[i]] || {}; node = node[segs[i]]; chain.push(node) }
    const leaf = segs[segs.length - 1]
    if (value.trim()) node[leaf] = value.trim()
    else {
      delete node[leaf]
      for (let i = chain.length - 1; i > 0; i--) {
        if (Object.keys(chain[i]).length === 0) delete chain[i - 1][segs[i - 1]]; else break
      }
    }
    if (!(await reportingWrite(`set ${path}`,
      () => api.updateULoop(id, { kind_config: { ...kc, token_overrides: ov } })))) return
    loadLoop(); loadTokens()
  }, [loop, id, loadLoop, loadTokens])

  if (notFound) return (
    <div className="grid h-full place-items-center text-on-surface-low">
      <div className="flex flex-col items-center gap-3">
        <p>This design loop no longer exists.</p>
        <Button variant="secondary" onClick={onBack}>Back</Button>
      </div>
    </div>
  )
  if (!loop) return <div className="grid h-full place-items-center text-on-surface-low">Loading…</div>

  const reactArtifacts = artifacts.filter((a) => a.kind === 'react')
  const docArtifacts = artifacts.filter((a) => a.kind === 'markdown')
  const overrideCount = Object.keys((loop.kind_config?.token_overrides as object) || {}).length

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={
          <div className="flex items-center gap-s min-w-0">
            <IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} />
            <div className="min-w-0 flex flex-col">
              <span data-type="title-m" className="truncate text-on-surface leading-tight" style={fvs(600)}>
                {loop.name || loop.task}
              </span>
              <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low">
                Design loop
                {
}
                {running && (
                  <>
                    <span className="inline-block size-1.5 rounded-pill"
                      style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
                    {connected ? 'Streaming' : 'Connecting…'}
                  </>
                )}
              </span>
            </div>
            <IconButton icon={linkCopied ? Check : Link2} label={linkCopied ? 'Link copied' : 'Copy link'} size={32} onClick={copyLink} />
          </div>
        }
        right={
          <HeaderActions>
            {
}
            {canAct('start') && <HeaderControl icon={Play} label="Start" variant="primary" priority="primary" onClick={() => act('start')} />}
            {canAct('pause') && <HeaderControl icon={Pause} label="Pause" variant="secondary" priority="primary" onClick={() => act('pause')} />}
            {canAct('resume') && <HeaderControl icon={Play} label="Resume" variant="primary" priority="primary" onClick={() => act('resume')} />}
            {canAct('stop') && <HeaderControl icon={Square} label={confirmStop ? 'Stop for good?' : 'Stop'} variant={confirmStop ? 'danger' : 'secondary'} onClick={() => { if (!confirmStop) { setConfirmStop(true); return } setConfirmStop(false); act('stop') }} />}
            {
}
            {active && <HeaderControl icon={MessageSquarePlus} label="Nudge" variant="secondary" ariaExpanded={nudgeOpen} onClick={() => setNudgeOpen((v) => !v)} />}
            {
}
            {onBuildWithChat && <HeaderControl icon={MessageSquare} label="Build with chat" variant="secondary" onClick={() => onBuildWithChat(loop)} />}
            {!active && <HeaderControl icon={Trash2} label={confirmDelete ? 'Confirm delete?' : 'Delete'} danger priority="low" onClick={del} />}
          </HeaderActions>
        }
      />

      {nudgeOpen && (
        <div className="shrink-0 px-2xl py-2 flex items-center gap-2 border-b border-outline-variant/30">
          <input autoFocus value={nudgeText} onChange={(e) => setNudgeText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') sendNudge() }}
            placeholder="Nudge the design loop — e.g. 'make the brand color warmer'"
            data-type="body-s" className="flex-1 h-9 rounded-md bg-surface-high px-3 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          <Button variant="primary" onClick={sendNudge}>Send</Button>
        </div>
      )}

      {
}
      <div className="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-outline-variant/30 px-2xl py-1.5"
        style={{ background: 'var(--color-surface-container)' }}>
        <DesignPhaseTrail plan={(loop.plan ?? []) as LoopPhase[]} phaseStatus={loop.phase_status || {}}
          cycle={loop.total_cycles || 0} active={active} complete={status === 'complete'} />
        <span data-type="caption" className="text-on-surface-var capitalize">{effectiveLoopStatus(status || '', loop.stop_reason).replace('_', ' ')}{(loop.total_cycles || active) ? ` · cycle ${shownCycle(loop.total_cycles, loop.status)}/${loop.max_cycles}` : ''}</span>
        {(loop.elapsed_seconds ?? 0) > 0 && (
          <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-low" title="Elapsed (running time)">
            <Clock size={11} />{fmtDesignElapsed(loop.elapsed_seconds ?? 0)}
          </span>
        )}
        {(loop.project_id || loop.tasks_project_id) && projName && (
          <button type="button" onClick={() => onOpenProject?.((loop.project_id || loop.tasks_project_id)!)} title={`Project: ${projName} — open`}
            data-type="caption" className="inline-flex items-center gap-1 rounded-pill px-2 h-5 hover:brightness-110"
            style={accentChip}>
            <FolderKanban size={11} /><span className="truncate max-w-[14rem]">{projName}</span>
          </button>
        )}
        <div data-type="caption" className="ml-auto inline-flex items-center rounded-md bg-surface-container p-0.5">
          {(['light', 'dark'] as Scheme[]).map((s) => (
            <button key={s} type="button" onClick={() => setScheme(s)}
              className={`px-2.5 h-5 rounded capitalize transition-colors ${scheme === s ? 'bg-surface-high text-on-surface' : 'text-on-surface-low'}`}>{s}</button>
          ))}
        </div>
      </div>

      { }
      <CockpitPromptBar prompt={loop.task || ''} />

      <div className="shrink-0 px-2xl pt-2 flex items-center gap-1 border-b border-outline-variant/30">
        {([['tokens', 'Tokens', Palette], ['canvas', 'Canvas', Box], ['palette', 'Palette', Upload], ['contrast', 'Contrast', Contrast], ['exports', 'Exports', Download]] as [Tab, string, any][]).map(([t, label, Icon]) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            data-type="body-s" className={`inline-flex items-center gap-1.5 px-3 h-9 border-b-2 -mb-px transition-colors ${tab === t ? 'border-primary text-on-surface' : 'border-transparent text-on-surface-low hover:text-on-surface'}`}>
            <Icon size={14} />{label}
            {t === 'canvas' && reactArtifacts.length > 0 && <span data-type="caption" className="text-on-surface-low">· {reactArtifacts.length}</span>}
          </button>
        ))}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2xl py-l">
        {tab === 'tokens' && <TokensView tokens={tokens} tokensErr={tokensErr} scheme={scheme} overrideCount={overrideCount} onRefresh={loadTokens} onOverride={setTokenOverride} readOnly={specFrozen} />}
        {tab === 'canvas' && <CanvasView artifacts={reactArtifacts} loopId={id} />}
        {tab === 'palette' && <PaletteView onApply={applyColorOverride} readOnly={specFrozen} />}
        {tab === 'contrast' && <ContrastView tokens={tokens} tokensErr={tokensErr} onRefresh={loadTokens} scheme={scheme} />}
        {tab === 'exports' && <ExportsView loop={loop} tokens={tokens} tokensErr={tokensErr} components={reactArtifacts} docs={docArtifacts} />}
      </div>
    </div>
  )
}


function fmtDesignElapsed(sec: number): string {
  if (!sec || sec < 1) return '0s'
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60)
  if (h) return `${h}h ${m}m`
  return m ? `${m}m ${s}s` : `${s}s`
}

function DesignPhaseTrail({ plan, phaseStatus, cycle, active, complete }: {
  plan: LoopPhase[]; phaseStatus: Record<string, string>; cycle: number; active: boolean; complete: boolean
}) {
  if (!plan.length) return null
  const key = (p: LoopPhase) => String(p.step || p.title || '').trim()
  const explicit = plan.findIndex((p) => ['active', 'running'].includes(phaseStatus[key(p)] || ''))
  const byCycle = active && cycle > 0 ? Math.min(plan.length - 1, Math.floor((cycle - 1) / Math.max(1, Math.ceil(30 / plan.length)))) : -1
  const activeIdx = complete ? plan.length : explicit >= 0 ? explicit : byCycle
  return (
    <div className="shrink-0 px-2xl pt-m">
      <div className="flex items-center gap-1 overflow-x-auto pb-1">
        {plan.map((p, i) => {
          const st = phaseStatus[key(p)]
          const done = complete || st === 'done' || (activeIdx >= 0 && i < activeIdx)
          const isActive = !done && i === activeIdx
          const Icon = done ? CheckCircle2 : isActive ? CircleDot : Circle
          const title = String(p.title || p.step || `Step ${i + 1}`)
          const obj = String((p as Record<string, unknown>).objective || '')
          return (
            <div key={i} className="flex items-center gap-1 shrink-0">
              {i > 0 && <span className="w-3 h-px bg-outline-variant/50" />}
              <span title={obj ? `${title} — ${obj}` : title}
                data-type="caption" className={`inline-flex items-center gap-1 rounded-pill px-2 h-6 ${isActive ? 'bg-primary-container text-on-primary-container' : done ? 'text-on-surface-low' : 'text-on-surface-low/70'}`}>
                {
}
                <Icon size={12} className="shrink-0" style={!isActive && done ? { color: 'var(--color-ok)' } : undefined} />
                <span className="truncate max-w-[10rem]">{title}</span>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function flattenRoleLeaves(o: unknown, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {}
  if (!o || typeof o !== 'object') return out
  for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
    if (k === 'comment') continue
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object') Object.assign(out, flattenRoleLeaves(v, key))
    else if (typeof v === 'string') out[key] = v
  }
  return out
}

function resolveSwatch(raw: unknown, scheme: Scheme): string {
  if (typeof raw === 'string') return raw
  if (!raw || typeof raw !== 'object') return ''
  const obj = raw as Record<string, unknown>
  if (typeof obj[scheme] === 'string') return obj[scheme] as string
  for (const v of Object.values(obj)) {
    const found = resolveSwatch(v, scheme)
    if (found) return found
  }
  return ''
}

function TokensUnread({ tokensErr, onRefresh }: { tokensErr?: unknown; onRefresh?: () => void }) {
  if (!tokensErr) return <div data-type="body-s" className="text-on-surface-low">Loading tokens…</div>
  return (
    <div role="alert" className="flex flex-col items-start gap-1">
      <p data-type="body-s" className="text-on-surface-low">
        Couldn’t read this design system’s tokens. Nothing is lost — the token set is still on the loop.
      </p>
      {
}
      {onRefresh && <Button variant="ghost-accent" size="xs" onClick={onRefresh}><RefreshCw size={13} /> Try again</Button>}
    </div>
  )
}


export function TokensView({ tokens, tokensErr, scheme, overrideCount, onRefresh, onOverride, readOnly }: { tokens: ResolvedTokens | null; tokensErr?: unknown; scheme: Scheme; overrideCount?: number; onRefresh?: () => void; onOverride?: (path: string, value: string) => void; readOnly?: boolean }) {
  if (!tokens) return <TokensUnread tokensErr={tokensErr} onRefresh={onRefresh} />
  const t = tokens.resolved
  const noComment = <V,>(o: Record<string, V> = {}): Record<string, V> =>
    Object.fromEntries(Object.entries(o).filter(([k]) => k !== 'comment')) as Record<string, V>
  const roles: Record<string, string> = flattenRoleLeaves(t.color?.semantic?.[scheme])
  const primitives: Record<string, Record<string, string>> = noComment(t.color?.primitive)
  const sizes: Record<string, string> = noComment(t.typography?.size)
  const families: Record<string, string> = noComment(t.typography?.family)
  const weights: Record<string, any> = noComment(t.typography?.weight)
  const spacing: Record<string, string> = noComment(t.spacing)
  const radius: Record<string, string> = noComment(t.radius)
  const shadows: Record<string, any> = t.shadow || {}
  const durations: Record<string, string> = noComment(t.motion?.duration)
  const easings: Record<string, string> = noComment(t.motion?.easing)
  const opacity: Record<string, any> = noComment(t.opacity)
  const blur: Record<string, string> = noComment(t.blur)
  const breakpoints: Record<string, string> = noComment(t.breakpoint)
  const gradients: Record<string, string> = noComment(t.gradient)
  const components: Record<string, any> = noComment(t.component)

  return (
    <div className="flex flex-col gap-2xl max-w-[64rem]">
      <div className="flex items-center gap-2">
        <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">{readOnly ? `Gideon default design system · ${scheme}` : `Resolved design system · ${scheme}`}</span>
        {!readOnly && (overrideCount ?? 0) > 0 && <span data-type="caption" className="rounded-pill px-2 h-5 inline-flex items-center" style={accentChip}>{overrideCount} override group{(overrideCount ?? 0) > 1 ? 's' : ''}</span>}
        {!readOnly && onRefresh && <button type="button" onClick={onRefresh} data-type="caption" className="ml-auto inline-flex items-center gap-1 text-on-surface-low hover:text-on-surface"><RefreshCw size={12} /> Refresh</button>}
      </div>

      <Section icon={Palette} title="Semantic roles">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
          {Object.entries(roles).map(([role, val]) => (
            <div key={role} className="flex items-center gap-2 rounded-md bg-surface-container px-2 py-1.5">
              <span className="size-7 shrink-0 rounded border border-outline-variant/40" style={{ background: val }} />
              <div className="min-w-0">
                <div data-type="caption" className="truncate text-on-surface">{role}</div>
                <div data-type="caption" className="truncate text-on-surface-low font-mono">{val}</div>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={Layers} title="Primitive scales">
        <div className="flex flex-col gap-2">
          {Object.entries(primitives).map(([name, scale]) => (
            <div key={name} className="flex flex-col gap-1">
              <span data-type="caption" className="text-on-surface-low capitalize">{name}</span>
              <div className="flex rounded-md overflow-hidden border border-outline-variant/30">
                {Object.entries(scale).map(([step, raw]) => {
                  const hex = resolveSwatch(raw, scheme)
                  return (
                    <div key={step} title={`${name}.${step} · ${hex || '—'}`} className="flex-1 h-8"
                          style={hex ? { background: hex } : undefined} />
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={Type} title="Type scale">
        <div className="flex flex-col gap-1.5">
          {Object.entries(sizes).map(([k, v]) => (
            <div key={k} className="flex items-baseline gap-3">
              <span data-type="caption" className="w-10 shrink-0 text-on-surface-low font-mono">{k}</span>
              <span className="truncate text-on-surface" style={{ fontSize: v, lineHeight: 1.2 }}>Ag {v}</span>
            </div>
          ))}
        </div>
      </Section>

      <div className="grid sm:grid-cols-2 gap-2xl">
        <Section icon={Ruler} title="Spacing">
          <div className="flex flex-col gap-1">
            {Object.entries(spacing).filter(([k]) => !['px', '0'].includes(k)).slice(0, 14).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2">
                <span data-type="caption" className="w-8 shrink-0 text-on-surface-low font-mono">{k}</span>
                <span className="h-3 rounded-sm bg-primary/60" style={{ width: v }} />
                <span data-type="caption" className="text-on-surface-low font-mono">{v}</span>
              </div>
            ))}
            <MoreRow total={Object.keys(spacing).filter(k => !['px', '0'].includes(k)).length} shown={14} noun="tokens" />
          </div>
        </Section>
        <Section icon={Box} title="Radius & elevation">
          {!readOnly && <div data-type="caption" className="text-on-surface-low mb-1">Click a radius to override it.</div>}
          <div className="flex flex-wrap gap-3">
            {Object.entries(radius).map(([k, v]) => (
              readOnly ? (
                <div key={k} className="flex flex-col items-center gap-1">
                  <span className="size-12 bg-surface-high border border-outline-variant/40" style={{ borderRadius: v }} />
                  <span data-type="caption" className="text-on-surface-low font-mono">{k} · {String(v)}</span>
                </div>
              ) : (
                <button key={k} type="button" title={`Override radius.${k} (now ${v})`}
                  onClick={async () => { const nv = await promptInput({ title: `Override radius.${k}`, label: `radius.${k} — new value (e.g. 0.5rem, 12px). Empty to reset to default.`, initial: String(v), required: false }); if (nv !== null) onOverride?.(`radius.${k}`, nv) }}
                  className="flex flex-col items-center gap-1 group">
                  <span className="size-12 bg-surface-high border border-outline-variant/40 transition-colors group-hover:border-primary" style={{ borderRadius: v }} />
                  <span data-type="caption" className="text-on-surface-low font-mono group-hover:text-on-surface">{k}</span>
                </button>
              )
            ))}
          </div>
          <div className="flex flex-wrap gap-4 mt-3">
            {['sm', 'md', 'lg', 'xl'].filter((k) => shadows[k]).map((k) => (
              <div key={k} className="flex flex-col items-center gap-1">
                <span className="size-12 rounded-lg bg-surface" style={{ boxShadow: shadows[k] }} />
                <span data-type="caption" className="text-on-surface-low font-mono">{k}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      {(Object.keys(families).length > 0 || Object.keys(weights).length > 0) && (
        <Section icon={Type} title="Typography">
          {!readOnly && <div data-type="caption" className="text-on-surface-low">Click a font family to override its stack.</div>}
          <div className="flex flex-col gap-2">
            {Object.entries(families).map(([k, v]) => (
              readOnly ? (
                <div key={k} className="flex items-baseline gap-3 text-left">
                  <span data-type="caption" className="w-16 shrink-0 text-on-surface-low font-mono capitalize">{k}</span>
                  <span data-type="body-m" className="truncate text-on-surface" style={{ fontFamily: String(v) }}>The quick brown fox</span>
                </div>
              ) : (
                <button key={k} type="button" title={`Override typography.family.${k}`}
                  onClick={async () => { const nv = await promptInput({ title: `Override typography.family.${k}`, label: `typography.family.${k} — new font stack (e.g. "Roboto, sans-serif"). Empty to reset.`, initial: String(v), required: false }); if (nv !== null) onOverride?.(`typography.family.${k}`, nv) }}
                  className="flex items-baseline gap-3 text-left group">
                  <span data-type="caption" className="w-16 shrink-0 text-on-surface-low font-mono capitalize group-hover:text-on-surface">{k}</span>
                  <span data-type="body-m" className="truncate text-on-surface group-hover:text-primary" style={{ fontFamily: String(v) }}>The quick brown fox</span>
                </button>
              )
            ))}
            {Object.keys(weights).length > 0 && (
              <div className="flex flex-wrap gap-3 mt-1">
                {
}
                {Object.entries(weights).map(([k, v]) => (
                  <span key={k} className="text-on-surface text-[0.9375rem]" title={`${k} · ${v}`} style={{ fontWeight: Number(v) || undefined }}>{k}</span>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}

      {Object.keys(gradients).length > 0 && (
        <Section icon={Palette} title="Gradients">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {Object.entries(gradients).map(([k, v]) => (
              <div key={k} className="flex flex-col gap-1">
                <span className="h-12 rounded-md border border-outline-variant/30" style={{ background: String(v) }} />
                <span data-type="caption" className="text-on-surface-low font-mono">{k}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {(Object.keys(durations).length > 0 || Object.keys(easings).length > 0) && (
        <Section icon={Repeat} title="Motion">
          <div className="flex flex-col gap-1.5">
            {Object.entries(durations).map(([k, v]) => (
              <div key={k} data-type="caption" className="flex items-center gap-2">
                <span className="w-20 shrink-0 text-on-surface-low font-mono">{k}</span>
                { }
                <span className="relative h-2 flex-1 max-w-[180px] rounded-pill bg-surface-high overflow-hidden">
                  <span className="absolute inset-y-0 left-0 w-2 rounded-pill bg-primary" style={{ animation: `pcMotionDemo ${v} ${easings.standard || 'ease'} infinite alternate` }} />
                </span>
                <span className="text-on-surface-low font-mono">{v}</span>
              </div>
            ))}
            {Object.keys(easings).length > 0 && (
              <div data-type="caption" className="flex flex-wrap gap-x-4 gap-y-1 mt-1 text-on-surface-low font-mono">
                {Object.entries(easings).map(([k, v]) => <span key={k} title={String(v)}>{k}</span>)}
              </div>
            )}
          </div>
          <style>{'@keyframes pcMotionDemo { from { transform: translateX(0) } to { transform: translateX(168px) } }'}</style>
        </Section>
      )}

      <div className="grid sm:grid-cols-2 gap-2xl">
        {Object.keys(opacity).length > 0 && (
          <Section icon={Layers} title="Opacity">
            <div className="flex flex-wrap gap-2">
              {Object.entries(opacity).map(([k, v]) => (
                <div key={k} className="flex flex-col items-center gap-1">
                  <span className="size-9 rounded-md bg-primary" style={{ opacity: Number(v) }} />
                  <span data-type="caption" className="text-on-surface-low font-mono">{k}</span>
                </div>
              ))}
            </div>
          </Section>
        )}
        {Object.keys(blur).length > 0 && (
          <Section icon={Box} title="Blur">
            <div className="flex flex-wrap gap-3">
              {Object.entries(blur).map(([k, v]) => (
                <div key={k} className="flex flex-col items-center gap-1">
                  <span className="size-9 rounded-md overflow-hidden relative bg-gradient-to-br from-primary to-accent">
                    <span className="absolute inset-0" style={{ backdropFilter: `blur(${v})`, WebkitBackdropFilter: `blur(${v})` }} />
                  </span>
                  <span data-type="caption" className="text-on-surface-low font-mono">{k}</span>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>

      {(Object.keys(breakpoints).length > 0 || Object.keys(components).length > 0) && (
        <Section icon={Ruler} title="Breakpoints & components">
          {Object.keys(breakpoints).length > 0 && (
            <div data-type="caption" className="flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(breakpoints).map(([k, v]) => (
                <span key={k} className="font-mono text-on-surface-var">{k}<span className="text-on-surface-low"> {v}</span></span>
              ))}
            </div>
          )}
          {Object.keys(components).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {Object.keys(components).map((k) => (
                <span key={k} data-type="caption" className="rounded-pill px-2 h-5 inline-flex items-center bg-surface-container text-on-surface-var">{k}</span>
              ))}
            </div>
          )}
        </Section>
      )}
    </div>
  )
}


function canvasOrderKey(loopId: string): string { return `gideon-design-canvas-order-${loopId}` }
function loadCanvasOrder(loopId: string): string[] {
  try { const v = JSON.parse(localStorage.getItem(canvasOrderKey(loopId)) || '[]'); return Array.isArray(v) ? v : [] }
  catch { return [] }
}
function saveCanvasOrder(loopId: string, order: string[]): void {
  try { localStorage.setItem(canvasOrderKey(loopId), JSON.stringify(order)) } catch {   }
}

function CanvasView({ artifacts, loopId }: { artifacts: Artifact[]; loopId: string }) {
  const [order, setOrder] = useState<string[]>(() => loadCanvasOrder(loopId))
  const [dragSlug, setDragSlug] = useState<string | null>(null)
  const [overSlug, setOverSlug] = useState<string | null>(null)

  const ordered = useMemo(() => {
    const bySlug = new Map(artifacts.map((a) => [a.slug, a]))
    const seen = new Set<string>()
    const out: Artifact[] = []
    for (const slug of order) { const a = bySlug.get(slug); if (a) { out.push(a); seen.add(slug) } }
    for (const a of artifacts) if (!seen.has(a.slug)) out.push(a)
    return out
  }, [artifacts, order])

  const commit = (slugs: string[]) => { setOrder(slugs); saveCanvasOrder(loopId, slugs) }

  const onDrop = (target: string) => {
    if (!dragSlug || dragSlug === target) { setDragSlug(null); setOverSlug(null); return }
    const slugs = ordered.map((a) => a.slug)
    const from = slugs.indexOf(dragSlug); const to = slugs.indexOf(target)
    if (from === -1 || to === -1) { setDragSlug(null); setOverSlug(null); return }
    slugs.splice(to, 0, slugs.splice(from, 1)[0])
    commit(slugs)
    setDragSlug(null); setOverSlug(null)
  }

  if (artifacts.length === 0) return (
    <div data-type="body-s" className="grid place-items-center h-full text-on-surface-low text-center">
      <div className="flex flex-col items-center gap-2 max-w-md">
        <Box size={28} className="opacity-50" />
        <p>No components yet. As the design loop generates React components (kind:react artifacts tagged to this loop), they render here live on the canvas.</p>
      </div>
    </div>
  )
  return (
    <div className="flex flex-col gap-l max-w-[64rem]">
      {ordered.length > 1 && (
        <p data-type="caption" className="text-on-surface-low">Drag the <GripVertical size={12} className="inline -mt-0.5" /> handle to compose the canvas — reorder how components stack. Your arrangement is remembered.</p>
      )}
      {ordered.map((a) => (
        <div key={a.slug}
          onDragOver={(e) => { if (dragSlug) { e.preventDefault(); if (overSlug !== a.slug) setOverSlug(a.slug) } }}
          onDrop={(e) => { e.preventDefault(); onDrop(a.slug) }}
          className={`rounded-lg transition-shadow ${overSlug === a.slug && dragSlug !== a.slug ? 'ring-2 ring-primary' : ''} ${dragSlug === a.slug ? 'opacity-50' : ''}`}>
          <CanvasComponent a={a} draggable={ordered.length > 1}
            onDragStart={() => setDragSlug(a.slug)} onDragEnd={() => { setDragSlug(null); setOverSlug(null) }} />
        </div>
      ))}
    </div>
  )
}

function CanvasComponent({ a, draggable, onDragStart, onDragEnd }: {
  a: Artifact; draggable?: boolean; onDragStart?: () => void; onDragEnd?: () => void
}) {
  const [jsx, setJsx] = useState<string | null>(a.content ?? null)
  useEffect(() => {
    let alive = true
    setJsx(a.content ?? null)
    if (a.content == null) {
      api.artifact(a.slug).then((full) => { if (alive) setJsx(full.content ?? '') }).catch(() => { if (alive) setJsx('') })
    }
    return () => { alive = false }
  }, [a.slug, a.version, a.content])
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        {
}
        {draggable && (
          <span draggable role="button" aria-label="Drag to reorder"
            onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', a.slug); onDragStart?.() }}
            onDragEnd={() => onDragEnd?.()}
            className="cursor-grab active:cursor-grabbing text-on-surface-low hover:text-on-surface">
            <GripVertical size={14} />
          </span>
        )}
        <span data-type="label-s" className="text-on-surface" style={fvs(600)}>{a.name}</span>
        <span data-type="caption" className="text-on-surface-low">v{a.version}</span>
      </div>
      {a.description && <p data-type="caption" className="text-on-surface-low">{a.description}</p>}
      {jsx === null ? <div data-type="caption" className="text-on-surface-low">Loading…</div>
        : jsx ? <ReactWidgetFrame jsx={jsx} title={a.name} />
        : <div data-type="caption" className="text-on-surface-low">(no content)</div>}
    </div>
  )
}


const CONTRAST_PAIRS: [string, string, string][] = [
  ['Body text', 'fg.default', 'bg.base'],
  ['Body on surface', 'fg.default', 'bg.surface'],
  ['Muted text', 'fg.muted', 'bg.surface'],
  ['Subtle text', 'fg.subtle', 'bg.surface'],
  ['Text on brand', 'fg.on-brand', 'brand.default'],
  ['Text on accent', 'fg.on-accent', 'accent.default'],
  ['Brand on base', 'brand.default', 'bg.base'],
  ['Accent on base', 'accent.default', 'bg.base'],
  ['Danger on base', 'danger.default', 'bg.base'],
  ['Success on base', 'success.default', 'bg.base'],
  ['Border vs surface', 'border.default', 'bg.surface'],
]

export function ContrastView({ tokens, tokensErr, onRefresh, scheme }: { tokens: ResolvedTokens | null; tokensErr?: unknown; onRefresh?: () => void; scheme: Scheme }) {
  if (!tokens) return <TokensUnread tokensErr={tokensErr} onRefresh={onRefresh} />
  const roles: Record<string, string> = flattenRoleLeaves(tokens.resolved?.color?.semantic?.[scheme])
  const rows = CONTRAST_PAIRS.map(([label, fgRole, bgRole]) => {
    const fg = toHex(roles[fgRole]); const bg = toHex(roles[bgRole])
    const ratio = (fg && bg) ? contrastRatio(fg, bg) : null
    return { label, fgRole, bgRole, fg, bg, ratio }
  })
  return (
    <div className="flex flex-col gap-l max-w-[56rem]">
      <p data-type="body-s" className="text-on-surface-low">
        WCAG contrast for the design system's key foreground/background pairings ({scheme} scheme).
        <span className="text-on-surface"> AA</span> needs ≥4.5 for body text (≥3.0 for large/UI),
        <span className="text-on-surface"> AAA</span> needs ≥7.0. Adjust your overrides on the
        Palette/Tokens tabs if a pairing fails.
      </p>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-3 rounded-md bg-surface-container px-3 py-2">
            { }
            <span data-type="label-s" className="grid place-items-center size-12 shrink-0 rounded-md border border-outline-variant/30"
              style={withWeight({ background: r.bg || 'transparent', color: r.fg || 'inherit' }, 600)}>Ag</span>
            <div className="min-w-0">
              <div data-type="body-s" className="text-on-surface">{r.label}</div>
              <div data-type="caption" className="truncate text-on-surface-low font-mono">{r.fgRole} on {r.bgRole}</div>
            </div>
            <div className="ml-auto flex items-center gap-2 shrink-0">
              <span data-type="body-s" className="font-mono text-on-surface tabular-nums">{r.ratio ? `${r.ratio.toFixed(2)}:1` : '—'}</span>
              {r.ratio != null && <ContrastBadges ratio={r.ratio} />}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ContrastBadges({ ratio }: { ratio: number }) {
  const tier = ratio >= 7 ? 'AAA' : ratio >= 4.5 ? 'AA' : ratio >= 3 ? 'AA Large' : 'Fail'
  const ok = ratio >= 4.5
  const warn = ratio >= 3 && ratio < 4.5
  const color = ok ? 'var(--color-success)' : warn ? 'var(--color-warning)' : 'var(--color-danger)'
  return (
    <span data-type="caption" className="inline-flex items-center rounded-pill px-2 h-5"
      style={{ background: `color-mix(in srgb, ${color} 16%, transparent)`, color }}>{tier}</span>
  )
}


function PaletteView({ onApply, readOnly }: { onApply: (scale: PaletteScale, hex: string) => void; readOnly?: boolean }) {
  const [palette, setPalette] = useState<string[]>([])
  const [imgUrl, setImgUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleFile = (file: File) => {
    setBusy(true)
    const url = URL.createObjectURL(file)
    setImgUrl(url)
    const img = new Image()
    img.onload = () => {
      try { setPalette(extractPalette(img, 6)) } catch { setPalette([]) }
      setBusy(false)
    }
    img.onerror = () => { setBusy(false) }
    img.src = url
  }

  return (
    <div className="flex flex-col gap-l max-w-[48rem]">
      <p data-type="body-s" className="text-on-surface-low">
        Upload a screenshot to extract its dominant color palette. Apply a color as the
        <span className="text-on-surface"> brand</span>, <span className="text-on-surface">accent</span>, or
        <span className="text-on-surface"> neutral</span> primitive — it cascades through every semantic role,
        component, and gradient (and, for neutral, every surface + text tone) that references it.
      </p>
      {readOnly && (
        <p data-type="body-s" className="rounded-md bg-surface-container px-3 py-2 text-on-surface-low">
          This design system is finalized — its tokens are locked. Extract a palette to inspect colors, but
          applying an override isn't available on a finished loop.
        </p>
      )}
      <div>
        <input ref={fileRef} type="file" accept="image/*" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f) }} />
        <Button variant="secondary" onClick={() => fileRef.current?.click()}><Upload size={14} className="mr-1.5" />Upload screenshot</Button>
      </div>
      {imgUrl && <img src={imgUrl} alt="uploaded" className="max-h-48 rounded-lg border border-outline-variant/40 object-contain" />}
      {busy && <div data-type="body-s" className="text-on-surface-low">Extracting palette…</div>}
      {palette.length > 0 && (
        <div className="flex flex-col gap-2">
          <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Extracted palette</span>
          <div className="flex flex-col gap-2">
            {palette.map((hex) => (
              <div key={hex} className="flex items-center gap-3 rounded-md bg-surface-container px-3 py-2">
                <span className="size-9 shrink-0 rounded border border-outline-variant/40" style={{ background: hex }} />
                <span data-type="body-s" className="font-mono text-on-surface">{hex}</span>
                {!readOnly && (
                  <div className="ml-auto flex gap-1.5">
                    {PALETTE_SCALES.map(({ scale, label }) => (
                      <Button key={scale} size="sm" variant="secondary" onClick={() => onApply(scale, hex)}>{label}</Button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


function ExportsView({ loop, tokens, tokensErr, components, docs }: { loop: Loop; tokens: ResolvedTokens | null; tokensErr?: unknown; components: Artifact[]; docs: Artifact[] }) {
  const base = safeFilename(loop.name || 'design-system')
  const tokenJson = useMemo(() => JSON.stringify({ overrides: loop.kind_config?.token_overrides || {}, resolved: tokens?.resolved || {} }, null, 2), [loop, tokens])
  const authoredDoc = useMemo(
    () => docs.find((a) => /design\.md|design[-_ ]?system|^design$/i.test(`${a.slug} ${a.name}`)) || docs[0] || null,
    [docs],
  )
  const fallbackMd = useMemo(() => buildDesignMd(loop, tokens), [loop, tokens])
  const [bundling, setBundling] = useState(false)
  const downloadDesignMd = async () => {
    let md = authoredDoc?.content ?? null
    if (authoredDoc && md == null) { try { md = (await api.artifact(authoredDoc.slug)).content ?? null } catch { md = null } }
    downloadText('DESIGN.md', md || fallbackMd, 'text/markdown')
  }

  const exportComponents = async () => {
    if (!components.length || bundling) return
    setBundling(true)
    try {
      const parts = await Promise.all(components.map(async (a) => {
        let jsx = a.content ?? null
        if (jsx == null) { try { jsx = (await api.artifact(a.slug)).content ?? '' } catch { jsx = '' } }
        const header = `// ── ${a.name}${a.description ? ` — ${a.description}` : ''} ──`
        return `${header}\n${jsx || '// (no content)'}`
      }))
      const banner = `/* ${loop.name || 'Design system'} — React components\n   Authored against the window React/ReactDOM globals (each defines a top-level App).\n   ${components.length} component${components.length > 1 ? 's' : ''}. */\n`
      downloadText(`${base}-components.jsx`, `${banner}\n${parts.join('\n\n')}\n`, 'text/jsx')
    } finally { setBundling(false) }
  }

  const unread = tokensErr
    ? 'The token set could not be read, so this export would be empty.'
    : 'Still reading the token set — this becomes available once it loads.'
  return (
    <div className="flex flex-col gap-l max-w-[48rem]">
      <p data-type="body-s" className="text-on-surface-low">Export the design system as reusable artifacts.</p>
      <ExportRow title="Token set (JSON)" desc="Resolved tokens + your overrides — feed into any build pipeline."
        disabled={!tokens} disabledReason={unread}
        onDownload={() => downloadText(`${base}-tokens.json`, tokenJson, 'application/json')} />
      <ExportRow title="CSS variables" desc={`A :root custom-property block (${tokens?.scheme || 'light'} scheme) — drop into any stylesheet.`}
        disabled={!tokens?.css}
        disabledReason={tokens ? 'This token set resolved no CSS block — nothing to export yet.' : unread}
        onDownload={() => tokens && downloadText(`${base}.css`, tokens.css, 'text/css')} />
      <ExportRow title={`React components${components.length ? ` (${components.length})` : ''}`}
        desc={components.length ? (bundling ? 'Bundling…' : 'The generated components bundled into one .jsx — drop into your project.') : 'No components yet — generate them on the Canvas first.'}
        disabled={!components.length || bundling} onDownload={exportComponents} />
      {
}
      <ExportRow title="DESIGN.md" desc={authoredDoc ? 'The design-system document authored by the loop — decisions, token reference, and usage.' : 'The design-system document — overrides, axes, and usage.'}
        disabled={!authoredDoc && !tokens}
        disabledReason={tokensErr
          ? 'The token set could not be read, and the loop hasn’t authored a DESIGN.md yet — so this would list no tokens.'
          : 'Still reading the token set, and the loop hasn’t authored a DESIGN.md yet.'}
        onDownload={downloadDesignMd} />
    </div>
  )
}

function ExportRow({ title, desc, onDownload, disabled, disabledReason }: { title: string; desc: string; onDownload: () => void; disabled?: boolean; disabledReason?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-surface-container px-4 py-3">
      <div className="min-w-0">
        <div data-type="label-s" className="text-on-surface" style={fvs(550)}>{title}</div>
        <div data-type="caption" className="text-on-surface-low">{desc}</div>
      </div>
      {
}
      <Button className="ml-auto shrink-0" variant="secondary" disabled={disabled} disabledReason={disabledReason} onClick={onDownload}><Download size={14} className="mr-1.5" />Download</Button>
    </div>
  )
}

function Section({ icon: Icon, title, children }: { icon: any; title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <div data-type="label-s" className="flex items-center gap-1.5 text-on-surface-var" style={fvs(600)}>
        <Icon size={14} />{title}
      </div>
      {children}
    </section>
  )
}


function extractPalette(img: HTMLImageElement, n: number): string[] {
  const W = 80, H = Math.max(1, Math.round((img.height / img.width) * 80))
  const canvas = document.createElement('canvas')
  canvas.width = W; canvas.height = H
  const ctx = canvas.getContext('2d')
  if (!ctx) return []
  ctx.drawImage(img, 0, 0, W, H)
  const data = ctx.getImageData(0, 0, W, H).data
  const buckets = new Map<string, { count: number; r: number; g: number; b: number }>()
  for (let i = 0; i < data.length; i += 4) {
    const a = data[i + 3]
    if (a < 128) continue
    const r = data[i], g = data[i + 1], b = data[i + 2]
    const max = Math.max(r, g, b), min = Math.min(r, g, b)
    if (max > 245 && min > 245) continue
    if (max < 18) continue
    const key = `${r >> 4}-${g >> 4}-${b >> 4}`
    const e = buckets.get(key)
    if (e) { e.count++; e.r += r; e.g += g; e.b += b }
    else buckets.set(key, { count: 1, r, g, b })
  }
  return [...buckets.values()]
    .sort((a, b) => b.count - a.count)
    .slice(0, n)
    .map((e) => rgbToHex(Math.round(e.r / e.count), Math.round(e.g / e.count), Math.round(e.b / e.count)))
}

function rgbToHex(r: number, g: number, b: number): string {
  return '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('')
}

function buildRamp(hex: string): Record<string, string> {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16)
  const [hue, sat, lit] = rgbToHsl(r, g, b)
  const L: Record<string, number> = {
    '50': 0.975, '100': 0.945, '200': 0.88, '300': 0.78, '400': 0.66,
    '500': lit, '600': lit * 0.82, '700': lit * 0.66, '800': lit * 0.5, '900': lit * 0.38, '950': lit * 0.24,
  }
  const out: Record<string, string> = {}
  for (const [step, l] of Object.entries(L)) {
    const s = step === '500' ? sat : Math.min(sat, l > lit ? sat * 0.9 : sat)
    const [rr, gg, bb] = hslToRgb(hue, s, Math.max(0, Math.min(1, l)))
    out[step] = rgbToHex(rr, gg, bb)
  }
  return out
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255
  const max = Math.max(r, g, b), min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let hh = 0
  if (max === r) hh = (g - b) / d + (g < b ? 6 : 0)
  else if (max === g) hh = (b - r) / d + 2
  else hh = (r - g) / d + 4
  return [hh / 6, s, l]
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  if (s === 0) { const v = Math.round(l * 255); return [v, v, v] }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s
  const p = 2 * l - q
  const t = (tc: number) => {
    if (tc < 0) tc += 1; if (tc > 1) tc -= 1
    if (tc < 1 / 6) return p + (q - p) * 6 * tc
    if (tc < 1 / 2) return q
    if (tc < 2 / 3) return p + (q - p) * (2 / 3 - tc) * 6
    return p
  }
  return [Math.round(t(h + 1 / 3) * 255), Math.round(t(h) * 255), Math.round(t(h - 1 / 3) * 255)]
}

function toHex(value?: string): string | null {
  if (!value) return null
  const v = value.trim().toLowerCase()
  if (v.startsWith('#')) {
    const h = v.slice(1)
    if (h.length === 3) return '#' + h.split('').map((c) => c + c).join('')
    if (h.length === 6) return '#' + h
    return null
  }
  const m = v.match(/^rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)/)
  if (m) return rgbToHex(+m[1], +m[2], +m[3])
  return null
}

function _luminance(hex: string): number {
  const h = hex.slice(1)
  const ch = [0, 2, 4].map((i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

function contrastRatio(fg: string, bg: string): number {
  const l1 = _luminance(fg); const l2 = _luminance(bg)
  const [hi, lo] = l1 >= l2 ? [l1, l2] : [l2, l1]
  return (hi + 0.05) / (lo + 0.05)
}

function buildDesignMd(loop: Loop, tokens: ResolvedTokens | null): string {
  const overrides = (loop.kind_config?.token_overrides as object) || {}
  const targets = String((loop.kind_config?.targets as string) || '').trim()
  const lines = [
    `# ${loop.name || 'Design System'}`, '',
    loop.task ? `> ${loop.task}` : '', targets ? `\n**Designing for:** ${targets}` : '', '',
    '## Overrides', '',
    Object.keys(overrides).length
      ? '```json\n' + JSON.stringify(overrides, null, 2) + '\n```'
      : '_No overrides yet — using the Gideon default token set._',
    '', '## Token axes', '',
    ...Object.keys(tokens?.resolved || {}).filter((k) => !['$schema', 'meta'].includes(k)).map((k) => `- \`${k}\``),
    '', '## Usage',
    'Reference tokens by their dotted path (e.g. `{color.primitive.brand.500}`, `{radius.lg}`). Override semantic roles and primitives — never component values directly — so light/dark and every component stay in lockstep.',
    '',
  ]
  return lines.filter((l) => l !== '').join('\n') + '\n'
}
