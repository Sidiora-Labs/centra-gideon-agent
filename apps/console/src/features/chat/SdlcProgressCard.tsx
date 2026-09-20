import { useEffect, useRef, useState } from 'react'
import { fvs, withWeight } from '../../shared/theme/fontWeight'
import { motion } from 'framer-motion'
import { Loader2, ArrowUpRight, CircleDot, CheckCircle2, Circle, AlertTriangle, HelpCircle, Clock, Search, Pause, Play, Square, Trash2 } from 'lucide-react'
import { api, type Loop } from '../../shared/data/api'
import { IconButton } from '../../shared/ui/IconButton'
import { loopStatusLabel, loopStatusTone, effectiveLoopStatus, LOOP_ACTION_SOURCE_STATUSES, type LoopAction } from '../../shared/data/loopStatus'
import { loopKindMeta } from '../../shared/data/loopKind'
import { foldRunSnapshot } from '../loops/runFold'
import { RunProgress } from '../loops/RunProgress'
import { messageEnter } from '../../shared/theme/motion'

const DONE_ST = new Set(['complete', 'failed', 'stopped'])


const SDLC_TOOLS = new Set([
  'project_run_create', 'project_run_start', 'project_run_status',
])

export interface SdlcRef { kind: 'code' | 'loop'; id: string; created: boolean }

export function sdlcRefFromTool(toolName: string | undefined, output: string | undefined): SdlcRef | null {
  if (!toolName || !SDLC_TOOLS.has(toolName) || !output) return null
  const m = output.match(/\/#\/(code|loops)\/([0-9a-f]{6,})/i)
  if (!m) return null
  return { kind: m[1].toLowerCase() === 'code' ? 'code' : 'loop', id: m[2], created: toolName.endsWith('_create') }
}

const LIVE = new Set(['running', 'intake', 'planning', 'review'])
const TERMINAL = new Set(['complete', 'failed', 'stopped'])

function fmtE(sec: number): string {
  if (!sec || sec < 60) return `${Math.max(0, Math.floor(sec))}s`
  const m = Math.floor(sec / 60); if (m < 60) return `${m}m`
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

export function SdlcProgressCard({ refObj, controllable = false, onDeleted }: {
  refObj: SdlcRef
  controllable?: boolean
  onDeleted?: () => void
}) {
  const { kind, id, created } = refObj
  const [entity, setEntity] = useState<Loop | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const gone = useRef(false)
  const loaded = useRef(false)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false
    gone.current = false
    loaded.current = false
    const tick = async () => {
      let st: string | undefined
      try {
        const e = await api.uLoop(id); if (!cancelled) { setEntity(e); setErr(null) } st = e?.status
        loaded.current = true
      } catch (e) {
        const status = (e as { status?: number })?.status
        if (status === 404) { gone.current = true; if (!cancelled) setErr('No longer exists (deleted).'); return }
        if (!cancelled && !loaded.current) setErr('Could not load progress.')
      }
      if (cancelled || gone.current) return
      if (st && TERMINAL.has(st)) return
      const delay = st && LIVE.has(st) ? 4000 : 10000
      timer = setTimeout(tick, delay)
    }
    void tick()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, id])

  const canAct = (action: LoopAction) => !!entity && LOOP_ACTION_SOURCE_STATUSES[action].has(entity.status)

  async function act(e: React.MouseEvent, action: Exclude<LoopAction, 'start'>) {
    e.preventDefault(); e.stopPropagation()
    if (busy) return
    setBusy(true)
    try { const next = await api.uLoopAction(id, action); setEntity(next) }
    catch {   }
    finally { setBusy(false) }
  }
  async function del(e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation()
    if (!confirmDel) { setConfirmDel(true); window.setTimeout(() => setConfirmDel(false), 4000); return }
    setConfirmDel(false); setBusy(true)
    try { await api.deleteULoop(id); gone.current = true; onDeleted?.() }
    catch { setErr('Could not delete.') }
    finally { setBusy(false) }
  }

  const status = entity ? effectiveLoopStatus(entity.status, entity.stop_reason) : (created ? 'ready' : '…')
  const dispKind = entity?.kind || (kind === 'code' ? 'code' : 'goal')
  const meta = loopKindMeta(dispKind)
  const title = entity?.name || `${meta.noun}${dispKind === 'code' ? ' project' : ''}`
  const href = `#/${dispKind === 'code' ? 'code' : 'loops'}/${id}`
  const Icon = meta.icon
  const cycles = entity?.total_cycles

  const vm = entity ? foldRunSnapshot({ ...entity, kind: dispKind, status }) : null
  const progress = vm?.progressLabel ?? ''
  const parked = vm?.parked ?? false
  const steps = vm?.steps ?? []
  const elapsed = entity?.elapsed_seconds ?? 0

  const findings = entity?.findings || []
  const recent = findings.slice(-3).reverse()

  const attention = (() => {
    if (!entity) return null
    if (status === 'needs_input') {
      const pq = entity.pending_question
      const q = (typeof pq === 'string' ? pq : pq?.question || '').trim()
      const why = (typeof pq === 'string' ? '' : pq?.why || '').trim()
      return { tone: 'info' as const, label: 'Needs your input', text: q || 'Waiting on your answer.', sub: why }
    }
    if (status === 'blocked' || status === 'failed' || status === 'stagnant') {
      const reason = (entity.error_message || '').trim()
      if (reason) return { tone: 'warn' as const, label: loopStatusLabel(status), text: reason, sub: '' }
    }
    return null
  })()

  const isPolling = !entity && !err

  return (
    <motion.div variants={messageEnter} initial="initial" animate="animate" className="my-1.5 overflow-hidden border border-outline-variant/40 bg-surface-low/40" style={{ borderRadius: 'var(--radius-md)' }}>
      { }
      <div className="flex items-center gap-2 px-3 py-2">
        <Icon size={15} className="shrink-0 text-primary" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(600)}>{title}</span>
        {progress && <span data-type="caption" className="shrink-0 text-on-surface-low">{progress}</span>}
        {typeof cycles === 'number' && cycles > 0 && <span data-type="caption" className="shrink-0 text-on-surface-low/70">· {cycles} cycles</span>}
        {elapsed > 0 && <span data-type="caption" className="shrink-0 inline-flex items-center gap-0.5 text-on-surface-low/70" title="Elapsed (running time)"><Clock size={10} />{fmtE(elapsed)}</span>}
        {
}
        {controllable && entity && (
          <span className="shrink-0 inline-flex items-center gap-0.5">
            {busy && <Loader2 size={11} className="animate-spin text-on-surface-low" />}
            {canAct('pause') && <IconButton icon={Pause} label="Pause" size={28} onClick={(e) => act(e, 'pause')} />}
            {canAct('resume') && <IconButton icon={Play} label="Resume" size={28} onClick={(e) => act(e, 'resume')} />}
            {canAct('stop') && <IconButton icon={Square} label="Stop" size={28} onClick={(e) => act(e, 'stop')} />}
            {DONE_ST.has(entity.status) && <IconButton icon={Trash2} size={28} tone="danger"
              label={confirmDel ? 'Click again to delete' : 'Delete'} onClick={del}
              className={confirmDel ? 'text-danger' : undefined} />}
          </span>
        )}
        <span data-type="caption" className="shrink-0 rounded-pill px-2 py-0.5" style={loopStatusTone(status)}>
          {isPolling ? <Loader2 size={10} className="inline animate-spin" /> : loopStatusLabel(status)}
        </span>
      </div>

      {
}
      {vm && <RunProgress vm={vm} />}

      { }
      {err && (
        <div data-type="caption" className="flex items-center gap-1.5 border-t border-outline-variant/30 px-3 py-1.5 text-on-surface-low">
          <AlertTriangle size={11} className="text-warn" /> {err}
        </div>
      )}

      {
}
      {attention && (
        <div data-type="caption" className="border-t border-outline-variant/30 px-3 py-2"
          style={{ background: attention.tone === 'info'
            ? 'color-mix(in srgb, var(--color-info) 9%, transparent)'
            : 'color-mix(in srgb, var(--color-warn) 9%, transparent)' }}>
          <div className="mb-0.5 inline-flex items-center gap-1.5"
            style={withWeight({ color: attention.tone === 'info' ? 'var(--color-info)' : 'var(--color-warn)' }, 600)}>
            {attention.tone === 'info' ? <HelpCircle size={11} /> : <AlertTriangle size={11} />} {attention.label}
          </div>
          <p className="whitespace-pre-wrap text-on-surface-var line-clamp-4">{attention.text}</p>
          {attention.sub && <p className="mt-0.5 whitespace-pre-wrap text-on-surface-low line-clamp-2">{attention.sub}</p>}
        </div>
      )}

      { }
      {steps.length > 0 && (
        <div className="border-t border-outline-variant/30 px-3 py-2">
          <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">{dispKind === 'goal' ? 'Sub-goals' : 'Stages'}</div>
          <ul className="flex flex-col gap-0.5">
            {steps.map((s, i) => (
              <li key={i} data-type="caption" className="flex items-center gap-1.5">
                {
}
                {dispKind === 'goal'
                  ? <span className="size-1 shrink-0 rounded-full bg-on-surface-low/50" />
                  : s.state === 'done' ? <CheckCircle2 size={12} className="shrink-0" style={{ color: 'var(--color-ok)' }} />
                  : s.state === 'active' ? <CircleDot size={12} className="shrink-0" style={{ color: parked ? 'var(--color-warn)' : 'var(--color-primary)' }} />
                  : <Circle size={12} className="shrink-0 text-on-surface-low/40" />}
                <span className={`min-w-0 truncate ${s.state === 'done' ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{s.label}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      { }
      {recent.length > 0 && (
        <div className="border-t border-outline-variant/30 px-3 py-2">
          <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">Recent activity</div>
          <ul className="flex flex-col gap-1.5">
            {recent.map((f, i) => {
              const srcN = Array.isArray((f as { sources_checked?: string[] }).sources_checked) ? (f as { sources_checked?: string[] }).sources_checked!.length : 0
              const newN = typeof (f as { new_findings_count?: number }).new_findings_count === 'number' ? (f as { new_findings_count?: number }).new_findings_count! : null
              return (
                <li key={i} data-type="caption" className="flex flex-col gap-0.5 text-on-surface-var">
                  <div className="flex gap-1.5">
                    <span className="shrink-0 text-on-surface-low/60">#{f.cycle}</span>
                    <span className="min-w-0 line-clamp-2">{f.summary || f.key_insight || '—'}</span>
                  </div>
                  { }
                  {(srcN > 0 || newN !== null) && (
                    <div data-type="caption" className="flex flex-wrap gap-1 pl-5 text-on-surface-low/80">
                      {srcN > 0 && <span className="inline-flex items-center gap-0.5 rounded-pill bg-surface-container px-1.5 py-px"><Search size={9} />{srcN} source{srcN === 1 ? '' : 's'}</span>}
                      {newN !== null && newN > 0 && <span className="inline-flex items-center gap-0.5 rounded-pill bg-surface-container px-1.5 py-px">+{newN} new</span>}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      { }
      <a href={href} data-type="caption" className="flex items-center gap-1 border-t border-outline-variant/30 px-3 py-1.5 text-primary transition-colors hover:bg-surface-low/70">
        Open {dispKind === 'code' ? 'in Code' : `in ${meta.noun}`} <ArrowUpRight size={12} />
      </a>
    </motion.div>
  )
}
