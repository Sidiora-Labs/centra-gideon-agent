import { useEffect, useRef, useState } from 'react'
import { unavailableWhen } from './unavailable'
import { motion, AnimatePresence } from 'framer-motion'
import { Loader2, Wrench, FileSearch, Check, MessageSquarePlus, CircleDot, Pencil, X, RefreshCw, AlertTriangle } from 'lucide-react'
import { TopBar } from './TopBar'
import { listItemEnter, stagger, spring, expr } from '../theme/motion'
import { fvs, withWeight } from '../theme/fontWeight'
import { confirm } from './dialog'
import { useChatSocket, type WsMessage } from '../data/useChatSocket'
import { cleanSay, toolDetail } from '../data/agentFeed'
import { planningTarget, type CommentTarget } from './content/commentTarget'
import type { PlanSession, PlanStep } from '../data/api'

export interface WalkthroughConfig {
  planSessionKey: (id: string) => string
  api: {
    getSession: (id: string) => Promise<PlanSession | null>
    start: (id: string) => Promise<unknown>
    approve: (id: string, stepId: string) => Promise<unknown>
    comment: (id: string, stepId: string, text: string) => Promise<unknown>
    edit: (id: string, stepId: string, markdown: string) => Promise<{ session: PlanSession }>
    isReady: (id: string) => Promise<boolean>
    retry?: (id: string) => Promise<unknown>
  }
  copy: {
    subtitle: string
    activityLabel: string
    activityEmpty: string
    cancel: string
  }
  renderArtifact: (kind: string, artifact: Record<string, unknown>, commentTarget?: CommentTarget) => React.ReactNode
}

type Line = { kind: 'tool'; label: string; detail?: string }

function lastSentence(raw: string): string {
  const clean = cleanSay(raw).replace(/\s+/g, ' ').trim()
  if (!clean) return ''
  const parts = clean.split(/(?<=[.!?])\s+/)
  return (parts[parts.length - 1] || '').trim()
}

export function PlanningWalkthrough({ id, cfg, onReady, onBack }: {
  id: string
  cfg: WalkthroughConfig
  onReady: () => void
  onBack: () => void
}) {
  const [lines, setLines] = useState<Line[]>([])
  const [ticker, setTicker] = useState('')
  const [heartbeat, setHeartbeat] = useState(0)
  const sayBuf = useRef('')
  const [session, setSession] = useState<PlanSession | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [err, setErr] = useState<string | null>(null)
  const [editText, setEditText] = useState<string | null>(null)
  const planKey = cfg.planSessionKey(id)
  const feedRef = useRef<HTMLDivElement>(null)
  const started = useRef(false)

  useChatSocket((m: WsMessage) => {
    if (String(m.data?.session ?? '') !== planKey) return
    setHeartbeat((h) => h + 1)
    if (m.type === 'tool_call') {
      const detail = toolDetail(String(m.data.input_preview ?? ''), String(m.data.purpose ?? ''))
      sayBuf.current = ''
      setTicker('')
      setLines((l) => [...l, { kind: 'tool' as const, label: String(m.data.tool ?? 'tool'), detail }].slice(-200))
    } else if (m.type === 'chat_chunk') {
      const piece = String(m.data.content ?? '')
      if (piece) {
        sayBuf.current = (sayBuf.current + piece).slice(-4000)
        setTicker(lastSentence(sayBuf.current))
      }
    }
  })

  const onReadyRef = useRef(onReady); onReadyRef.current = onReady
  useEffect(() => {
    let alive = true
    let gone = false
    const tick = async () => {
      let s: PlanSession | null = null
      try {
        s = await cfg.api.getSession(id)
      } catch (e) {
        if ((e as { status?: number })?.status === 404) { gone = true; if (alive) onBack() }
        return
      }
      if (!alive || gone) return
      if (s) setSession(s)
      if (!s && !started.current) {
        started.current = true
        await cfg.api.start(id).catch(() => {})
        return
      }
      if (await cfg.api.isReady(id).catch(() => false)) { if (alive) onReadyRef.current() }
    }
    tick()
    const iv = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(iv) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  useEffect(() => { feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }) }, [lines])

  const currentId = session?.steps?.find((s) => s.status !== 'approved')?.id ?? null
  useEffect(() => { setComment(''); setEditText(null) }, [currentId])

  const [quietMs, setQuietMs] = useState(0)
  const lastProgress = useRef(Date.now())
  const sessionSig = JSON.stringify(session?.steps?.map((s) => [s.id, s.status, !!s.artifact?.markdown]) ?? [])
  useEffect(() => { lastProgress.current = Date.now(); setQuietMs(0) }, [sessionSig, lines.length, ticker, heartbeat])
  useEffect(() => {
    const iv = setInterval(() => setQuietMs(Date.now() - lastProgress.current), 5000)
    return () => clearInterval(iv)
  }, [])
  const STALL_MS = 180_000
  const stalled = quietMs > STALL_MS

  const steps = session?.steps ?? []
  const current = steps.find((s) => s.status !== 'approved') ?? null

  async function requestApprove(step: PlanStep) {
    if (comment.trim()) {
      if (!(await confirm({
        title: 'Approve this step?',
        body: 'You have an unsent comment for this step. Approving as-is discards it and moves on. Send the comment instead if you want it folded into a redraft.',
        confirmLabel: 'Approve as-is',
      }))) return
    }
    void doApprove(step)
  }
  async function doApprove(step: PlanStep) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await cfg.api.approve(id, step.id); setComment('') }
    catch (e) { setErr(`Couldn't approve this step: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  async function sendComment(step: PlanStep) {
    const text = comment.trim(); if (!text) return
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await cfg.api.comment(id, step.id, text); setComment('') }
    catch (e) { setErr(`Couldn't send that comment: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  async function retry() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try { await (cfg.api.retry ?? cfg.api.start)(id) }
    catch (e) { setErr(`Couldn't restart planning: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
    const s = await cfg.api.getSession(id).catch(() => null); if (s) setSession(s)
  }
  async function saveEdit(step: PlanStep) {
    if (editText === null) return
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr(null)
    try {
      const r = await cfg.api.edit(id, step.id, editText)
      if (r?.session) setSession(r.session)
      setEditText(null)
    }
    catch (e) { setErr(`Couldn't save your edit: ${(e as Error).message || 'unknown error'}`) }
    finally { setBusy(false); inFlight.current = false }
  }

  const approvedCount = steps.filter((s) => s.status === 'approved').length

  const awaitingReview = current?.status === 'awaiting_review'
  const headerLabel = awaitingReview ? 'Awaiting your review' : stalled ? 'Planning paused' : 'Planning…'

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2">
          {awaitingReview
            ? <CircleDot size={16} className="text-primary" />
            : <Loader2 size={16} className={stalled ? 'text-on-surface-low' : 'animate-spin text-primary'} />}
          <span data-type="title-l" className="text-on-surface">{headerLabel}</span>
          <span data-type="body-s" className="text-on-surface-low">{cfg.copy.subtitle}</span>
        </div>}
        right={<button type="button" onClick={onBack}
          data-type="body-s"
          className="rounded-pill px-3 h-9 text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface">{cfg.copy.cancel}</button>}
      />
      {
}
      <div className="min-h-0 flex-1 px-l py-l">
        <div className="mx-auto flex h-full min-h-0 w-full flex-col gap-4 lg:flex-row" style={{ maxWidth: 'var(--content-width)' }}>
          { }
          <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
            <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
              <div data-type="label-s" className="mb-2 text-on-surface-var" style={fvs(550)}>
                Planning steps {steps.length ? `(${approvedCount}/${steps.length} approved)` : ''}
              </div>
              {steps.length === 0 ? (
                session?.design_error ? (
                  <div data-type="body-s" className="flex flex-col items-start gap-1.5">
                    <p className="inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
                      <AlertTriangle size={14} /> Planning didn't produce a plan
                    </p>
                    <p className="text-on-surface-low">{session.design_error}</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry planning
                    </button>
                  </div>
                ) : stalled ? (
                  <div data-type="body-s" className="flex flex-col items-start gap-1.5">
                    <p className="text-on-surface-low">The planner has been quiet for a while — it may still be investigating, or it may have hit an error or an unavailable model. Retry only if it seems stuck.</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry planning
                    </button>
                  </div>
                ) : (
                  <p data-type="body-s" className="text-on-surface-low">The planner is preparing the steps…</p>
                )
              ) : (
                <motion.ol className="flex flex-col gap-1"
                  variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
                  {steps.map((s, i) => (
                    <motion.li key={s.id} variants={listItemEnter} data-type="body-s" className="flex items-center gap-2">
                      {s.status === 'approved' ? <Check size={14} className="shrink-0 text-ok" />
                        : s.status === 'awaiting_review' ? <CircleDot size={14} className="shrink-0 text-primary" />
                        : s.status === 'running' ? <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
                        : <span className="ml-0.5 mr-0.5 h-2.5 w-2.5 shrink-0 rounded-full border border-outline-variant" />}
                      <span className={s.status === 'approved' ? 'text-on-surface-low line-through' : 'text-on-surface'}>{i + 1}. {s.title}</span>
                      <span data-type="caption" className="text-on-surface-low">{s.kind.replace(/_/g, ' ')}</span>
                    </motion.li>
                  ))}
                </motion.ol>
              )}
            </div>

            {
}
            <AnimatePresence mode="wait" initial={false}>
              {current && (
              <motion.div key={current.id}
                initial={{ opacity: 0, x: expr(16, 0.4) }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -expr(16, 0.4) }}
                transition={spring.spatialFast}
                className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
                <div className="mb-1.5 flex items-center gap-2">
                  <span data-type="title-s" className="text-on-surface">{current.title}</span>
                  <span data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">{current.kind.replace(/_/g, ' ')}</span>
                </div>
                {current.objective && <p data-type="body-s" className="mb-2 text-on-surface-low">{current.objective}</p>}
                {current.status === 'awaiting_review' ? (
                  <>
                    {editText !== null ? (
                      <div className="flex flex-col gap-2">
                        {!(typeof current.artifact?.markdown === 'string' && current.artifact.markdown.trim()) && (
                          <p data-type="caption" className="text-on-surface-low">
                            This step's structured detail is preserved — this box only adds/edits a prose summary alongside it.
                          </p>
                        )}
                        <textarea autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} rows={14}
                          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!busy) void saveEdit(current) } }}
                          placeholder="Write the step's prose body in markdown…"
                          data-type="caption"
                          className="w-full resize-y rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 font-mono text-on-surface outline-none focus:border-primary placeholder:text-on-surface-low" />
                        <div className="flex items-center gap-2">
                          <button type="button" disabled={busy} onClick={() => saveEdit(current)}
                            data-type="body-s"
                            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-on-primary disabled:opacity-50">
                            <Check size={14} /> Save edits
                          </button>
                          <button type="button" disabled={busy} onClick={() => setEditText(null)}
                            data-type="body-s"
                            className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var">
                            <X size={14} /> Cancel
                          </button>
                        </div>
                        {err && (
                          <p role="alert" data-type="body-s" style={{ color: 'var(--color-danger)' }}>{err}</p>
                        )}
                      </div>
                    ) : (
                      <div className="group/art relative">
                        {
}
                        <button type="button" title="Edit this artifact"
                          onClick={() => setEditText(typeof current.artifact?.markdown === 'string' ? current.artifact.markdown as string : '')}
                          data-type="caption"
                          className="absolute right-0 top-0 z-10 inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-on-surface-low opacity-60 transition-opacity hover:bg-surface-high hover:text-on-surface hover:opacity-100 focus-visible:opacity-100 group-hover/art:opacity-100">
                          <Pencil size={12} /> Edit
                        </button>
                        {cfg.renderArtifact(current.kind, current.artifact ?? {}, planningTarget((message) => { void cfg.api.comment(id, current.id, message) }))}
                      </div>
                    )}
                    {
}
                    {editText === null && !!current.comments?.length && (
                      <motion.div className="mt-3 flex flex-col gap-1.5"
                        variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
                        {current.comments.map((c, i) => (
                          <motion.div key={i} variants={listItemEnter} data-type="caption" className="flex items-start gap-1.5 rounded-lg border border-outline-variant/40 bg-surface-container/40 px-2.5 py-1.5">
                            <MessageSquarePlus size={12} className="mt-0.5 shrink-0 text-on-surface-low" />
                            <span className="min-w-0 whitespace-pre-wrap text-on-surface-var">{c.text}</span>
                          </motion.div>
                        ))}
                      </motion.div>
                    )}
                    {editText === null && <div className="mt-3 flex flex-col gap-2">
                      <textarea value={comment} onChange={(e) => setComment(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!busy && comment.trim()) void sendComment(current) } }}
                        placeholder="Comment to refine this step (⌘↵ to send), or approve as-is…" rows={2}
                        data-type="body-s"
                        className="w-full resize-none rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 text-on-surface outline-none focus:border-primary" />
                      <div className="flex items-center gap-2">
                        <button type="button" disabled={busy} onClick={() => requestApprove(current)}
                          data-type="body-s"
                          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-on-primary disabled:opacity-50">
                          <Check size={14} /> Approve & continue
                        </button>
                        <button type="button" onClick={() => sendComment(current)}
                          {...unavailableWhen(!comment.trim(), 'Write a comment first', { busy })}
                          data-type="body-s"
                          className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
                          <MessageSquarePlus size={14} /> Send comment & redraft
                        </button>
                      </div>
                      {err && (
                        <p role="alert" data-type="body-s" style={{ color: 'var(--color-danger)' }}>{err}</p>
                      )}
                    </div>}
                  </>
                ) : stalled ? (
                  <div data-type="body-s" className="flex flex-col items-start gap-1.5">
                    <p className="text-on-surface-low">This step has been quiet for a while — the planner may still be working, or it may have errored / the model is unavailable. Retry only if it seems stuck.</p>
                    <button type="button" disabled={busy} onClick={retry}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant/60 px-3 py-1.5 text-on-surface-var disabled:opacity-50">
                      <RefreshCw size={13} /> Retry this step
                    </button>
                    {err && (
                      <p role="alert" data-type="body-s" style={{ color: 'var(--color-danger)' }}>{err}</p>
                    )}
                  </div>
                ) : (
                  <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low">
                    <Loader2 size={14} className="animate-spin text-primary" />
                    {current.comments?.length ? 'Re-drafting with your feedback…' : 'Drafting this step…'}
                  </div>
                )}
              </motion.div>
              )}
            </AnimatePresence>
          </div>

          {
}
          <aside className="flex min-h-0 shrink-0 flex-col rounded-xl border border-outline-variant/50 bg-surface-container/60 lg:w-[340px] max-lg:max-h-[34vh]">
            <div data-type="label-s" className="flex items-center gap-1.5 border-b border-outline-variant/40 px-3.5 py-2.5 text-on-surface-var" style={fvs(550)}>
              <FileSearch size={14} className="text-primary" /> {cfg.copy.activityLabel}
            </div>
            {
}
            <div ref={feedRef} className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden px-3.5 py-3 text-[0.8125rem]">
              {lines.length === 0 && !ticker ? (
                <p className="text-on-surface-low">{cfg.copy.activityEmpty}</p>
              ) : (
                <>
                  {lines.map((l, i) => (
                    <motion.div key={i} variants={listItemEnter} initial="initial" animate="animate"
                      className="flex min-w-0 items-start gap-1.5">
                      <Wrench size={12} className="mt-0.5 shrink-0 text-on-surface-low" />
                      <span className="min-w-0 flex-1 break-words text-on-surface-low">
                        <b className="text-on-surface-var">{l.label}</b>{l.detail ? <> · <code data-type="caption" className="break-all text-on-surface-low/90">{l.detail}</code></> : ''}
                      </span>
                    </motion.div>
                  ))}
                  {ticker && (
                    <div className="flex min-w-0 items-start gap-1.5">
                      <Loader2 size={12} className="mt-0.5 shrink-0 animate-spin text-primary" />
                      <span key={ticker} data-type="body-s" className="text-shimmer min-w-0 flex-1 truncate" title={ticker}>{ticker}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}

export function ArtifactSection({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div data-type="caption" className="flex items-center gap-1.5 text-on-surface-var" style={fvs(600)}>{icon} {label}</div>
      {children}
    </div>
  )
}

export const artifactList = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x) => x && typeof x === 'object') as Record<string, unknown>[] : []
export const artifactStrings = (v: unknown): string[] =>
  Array.isArray(v) ? (v as unknown[]).map((x) => typeof x === 'string' ? x : String((x as Record<string, unknown>)?.title ?? (x as Record<string, unknown>)?.text ?? '')).filter(Boolean) : []
