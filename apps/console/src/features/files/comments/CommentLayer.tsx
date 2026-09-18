import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { MessageSquarePlus, X, Pencil, Check, Send, MessagesSquare, ChevronUp, ChevronDown } from 'lucide-react'
import { Button } from '../../../shared/ui/Button'
import { TextArea } from '../../../shared/ui/forms'
import { fvs } from '../../../shared/theme/fontWeight'
import { IconButton } from '../../../shared/ui/IconButton'
import { Modal } from '../../../shared/ui/Modal'
import { SelectionPill } from '../../../shared/ui/SelectionPill'
import { spring } from '../../../shared/theme/motion'
import { commentStore, useComments, findCoords, captureContext, formatCommentsMessage, type DocComment } from './commentStore'

export function CommentLayer({ scrollRef, docId, docLabel, docPath, content, onSubmit }: {
  scrollRef: React.RefObject<HTMLElement | null>
  docId: string
  docLabel: string
  docPath?: string
  content?: string
  onSubmit: (message: string, docPaths: string[]) => void
}) {
  const all = useComments()
  const [sel, setSel] = useState<{ x: number; y: number; text: string } | null>(null)
  const [composing, setComposing] = useState<{ x: number; y: number; quote: string } | null>(null)
  const [draft, setDraft] = useState('')
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const composerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    const inComposer = (t: EventTarget | null) =>
      (btnRef.current && t instanceof Node && btnRef.current.contains(t)) ||
      (composerRef.current && t instanceof Node && composerRef.current.contains(t))
    const onUp = (e: MouseEvent) => {
      if (inComposer(e.target)) return
      const s = window.getSelection()
      const text = s?.toString().trim() ?? ''
      if (!text || !s || s.rangeCount === 0) { setSel(null); return }
      const range = s.getRangeAt(0)
      if (!root.contains(range.commonAncestorContainer)) { setSel(null); return }
      const r = range.getBoundingClientRect()
      const pr = root.getBoundingClientRect()
      setSel({
        x: r.left - pr.left + root.scrollLeft + r.width / 2,
        y: r.top - pr.top + root.scrollTop - 8,
        text,
      })
    }
    const onDown = (e: MouseEvent) => { if (!inComposer(e.target)) { setSel(null) } }
    document.addEventListener('mouseup', onUp)
    root.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('mouseup', onUp); root.removeEventListener('mousedown', onDown) }
  }, [scrollRef])

  function openComposer() {
    if (!sel) return
    setComposing({ x: sel.x, y: sel.y, quote: sel.text })
    setDraft('')
    setSel(null)
    window.getSelection()?.removeAllRanges()
  }
  function saveComposer() {
    const c = draft.trim()
    if (!composing || !c) return
    const coords = content ? findCoords(content, composing.quote) : undefined
    const context = content && coords ? captureContext(content, composing.quote, coords.line, coords.column) : undefined
    commentStore.add({ docId, docLabel, docPath, quote: composing.quote, comment: c, line: coords?.line, column: coords?.column, context })
    setComposing(null); setDraft('')
  }

  return (
    <>
      { }
      {sel && (
        <SelectionPill ref={btnRef} icon={MessageSquarePlus} label="Comment" x={sel.x} y={sel.y} onPress={openComposer} />
      )}

      { }
      {composing && (
        <div ref={composerRef} className="absolute z-40 -translate-x-1/2 w-[min(22rem,80vw)] rounded-xl bg-surface-highest p-m shadow-xl ring-1 ring-outline-variant/50"
          style={{ left: composing.x, top: composing.y }}>
          <div className="mb-2 max-h-16 overflow-y-auto rounded-md bg-surface-low px-2 py-1.5 text-on-surface-var text-[0.75rem] italic line-clamp-3">
            “{composing.quote}”
          </div>
          <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Add a comment"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveComposer() } if (e.key === 'Escape') setComposing(null) }}
            placeholder="Add a comment…  (↵ to save, ⇧↵ for newline)"
            rows={3}
            className="w-full resize-none rounded-md bg-surface-container px-2.5 py-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          <div className="mt-2 flex justify-end gap-s">
            <Button variant="ghost" size="sm" onClick={() => setComposing(null)}>Cancel</Button>
            <Button size="sm" onClick={saveComposer} disabled={!draft.trim()}
              disabledReason={!draft.trim() ? 'Write a comment first' : undefined}>Comment</Button>
          </div>
        </div>
      )}

      {
}
      {all.length > 0 && <DeckPortal scrollRef={scrollRef}>
        <CommentDeck comments={all} activeDocId={docId} onSubmit={onSubmit} />
      </DeckPortal>}
    </>
  )
}

function DeckPortal({ scrollRef, children }: { scrollRef: React.RefObject<HTMLElement | null>; children: React.ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null)
  useEffect(() => {
    const parent = scrollRef.current?.parentElement ?? null
    if (parent) {
      if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative'
    }
    setHost(parent)
  }, [scrollRef])
  if (!host) return null
  return createPortal(children, host)
}

function CommentDeck({ comments, activeDocId, onSubmit }: {
  comments: DocComment[]; activeDocId: string
  onSubmit: (message: string, docPaths: string[]) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [instructions, setInstructions] = useState('')
  const ordered = [...comments].reverse()
  const PEEK = 7
  const peekCount = Math.min(ordered.length - 1, 3)
  const docCount = new Set(comments.map((c) => c.docId)).size
  const lead = ordered.find((c) => c.docId === activeDocId) ?? ordered[0]
  const leadMuted = lead.docId !== activeDocId
  const spread = docCount > 1 || leadMuted

  return (
    <div className="pointer-events-none absolute inset-0 z-30 flex flex-col items-center justify-end px-l">
      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.div key="open" initial={{ y: 24, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 24, opacity: 0 }} transition={spring.spatialFast}
            className="pointer-events-auto mx-auto flex w-full max-w-[var(--content-width)] flex-col overflow-hidden rounded-t-xl border border-b-0 border-outline-variant/50 bg-surface/95 shadow-2xl ring-1 ring-black/5 backdrop-blur-md"
            style={{ maxHeight: '50%' }}>
            <div className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-l py-2">
              <MessagesSquare size={15} className="text-primary" />
              <span className="text-on-surface text-[0.8125rem]" style={fvs(500)}>
                {comments.length} comment{comments.length === 1 ? '' : 's'}
              </span>
              <span className="text-on-surface-low text-[0.75rem]">across {new Set(comments.map((c) => c.docId)).size} document{new Set(comments.map((c) => c.docId)).size === 1 ? '' : 's'}</span>
              <div className="ml-auto flex items-center gap-s">
                <Button size="sm" onClick={() => setSubmitting(true)}><Send size={14} /> Submit to AI</Button>
                <IconButton icon={ChevronDown} label="Collapse" size={32} onClick={() => setExpanded(false)} />
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-l">
              {ordered.map((c) => <CommentCard key={c.id} c={c} muted={c.docId !== activeDocId} />)}
            </div>
          </motion.div>
        ) : (
          <motion.button key="closed" type="button" onClick={() => setExpanded(true)}
            initial={{ y: 24, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 24, opacity: 0 }} transition={spring.spatialFast}
            className="pointer-events-auto relative mx-auto block w-full max-w-[var(--content-width)] text-left"
            style={{ paddingTop: peekCount * PEEK }}>
            { }
            {ordered.slice(1, 4).map((c, i) => (
              <span key={c.id} aria-hidden
                className="absolute inset-x-0 rounded-t-xl border border-b-0 border-outline-variant/50 bg-surface-container shadow-md"
                style={{ bottom: 0, top: (peekCount - 1 - i) * PEEK, zIndex: 3 - i, transform: `scaleX(${1 - (i + 1) * 0.025})` }} />
            ))}
            { }
            <div className="relative z-10 rounded-t-xl border border-b-0 border-outline-variant/50 bg-surface/95 shadow-xl ring-1 ring-black/5 backdrop-blur-md">
              <div className="flex items-center gap-2 px-l pt-2.5">
                <MessagesSquare size={14} className="text-primary" />
                <span className="text-on-surface text-[0.75rem]" style={fvs(500)}>{comments.length} comment{comments.length === 1 ? '' : 's'}</span>
                <span className={`min-w-0 flex-1 truncate text-on-surface-low text-[0.75rem] ${leadMuted ? 'opacity-65' : ''}`} title={lead.docLabel}>· {lead.docLabel}</span>
                {spread && (
                  <span data-testid="comment-deck-spread" className="shrink-0 rounded-pill bg-surface-high px-2 text-on-surface-low text-[0.6875rem]" style={fvs(500)}>
                    {docCount > 1 ? `${docCount} documents` : 'another document'}
                  </span>
                )}
                {
}
                <span role="button" tabIndex={0}
                  onClick={(e) => { e.stopPropagation(); setSubmitting(true) }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); setSubmitting(true) } }}
                  className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-primary px-2.5 h-6 text-[0.75rem] text-on-primary transition-opacity hover:opacity-90"
                  style={fvs(500)}>
                  <Send size={11} /> Submit to AI
                </span>
                <ChevronUp size={14} className="text-on-surface-low" />
              </div>
              <div data-testid="comment-deck-lead" className={`px-l pb-2.5 pt-1 ${leadMuted ? 'opacity-65' : ''}`}>
                <div className="mb-1 truncate text-on-surface-var text-[0.75rem] italic">“{lead.quote}”</div>
                <div className="line-clamp-1 text-on-surface text-[0.8125rem]">{lead.comment}</div>
              </div>
            </div>
          </motion.button>
        )}
      </AnimatePresence>

      {submitting && (
        <SubmitModal count={comments.length}
          instructions={instructions} setInstructions={setInstructions}
          onCancel={() => setSubmitting(false)}
          onConfirm={() => {
            const message = formatCommentsMessage(comments, instructions.trim())
            const docPaths = [...new Set(comments.map((c) => c.docPath).filter((p): p is string => !!p))]
            onSubmit(message, docPaths)
            commentStore.removeMany(comments.map((c) => c.id))
            setSubmitting(false); setInstructions(''); setExpanded(false)
          }} />
      )}
    </div>
  )
}

function CommentCard({ c, muted }: { c: DocComment; muted: boolean }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(c.comment)
  return (
    <div className={`flex w-full shrink-0 flex-col rounded-lg border border-outline-variant/50 bg-surface-container p-3 ${muted ? 'opacity-65' : ''}`}>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-on-surface-low text-[0.75rem]" title={c.docLabel}>{c.docLabel}</span>
        {!editing && <IconButton icon={Pencil} label="Edit comment" size={26} onClick={() => { setVal(c.comment); setEditing(true) }} />}
        <IconButton icon={X} label="Remove comment" size={26} tone="danger" onClick={() => commentStore.remove(c.id)} />
      </div>
      <div className="mb-2 max-h-20 overflow-y-auto rounded-md bg-surface-low px-2 py-1.5 text-on-surface-var text-[0.75rem] italic">“{c.quote}”</div>
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <textarea autoFocus value={val} onChange={(e) => setVal(e.target.value)} rows={3} aria-label="Edit comment"
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); const t = val.trim(); if (t) { commentStore.update(c.id, { comment: t }); setEditing(false) } } if (e.key === 'Escape') setEditing(false) }}
            className="w-full resize-none rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          <div className="flex justify-end gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
            <Button size="sm" onClick={() => { const t = val.trim(); if (t) { commentStore.update(c.id, { comment: t }); setEditing(false) } }}><Check size={13} /> Save</Button>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto text-on-surface text-[0.8125rem] leading-relaxed whitespace-pre-wrap">{c.comment}</div>
      )}
    </div>
  )
}

function SubmitModal({ count, instructions, setInstructions, onCancel, onConfirm }: {
  count: number; instructions: string; setInstructions: (v: string) => void
  onCancel: () => void; onConfirm: () => void
}) {
  return (
    <Modal title="Submit comments to AI" icon={<Send size={18} className="text-primary" />} onClose={onCancel}>
      <div className="flex flex-col gap-m p-l" style={{ minWidth: 420 }}>
        <p className="text-on-surface-var text-[0.8125rem]">
          Hand {count} comment{count === 1 ? '' : 's'} to a new AI chat with the referenced document{count === 1 ? '' : 's'} in context, so the agent can address them.
        </p>
        <div className="flex flex-col gap-1.5">
          <label className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Additional instructions (optional)</label>
          <TextArea autoFocus value={instructions} onChange={setInstructions} rows={4} size="sm"
            ariaLabel="Additional instructions (optional)"
            placeholder="e.g. Apply these edits directly, or just propose changes for me to review first…" />
        </div>
        <div className="flex justify-end gap-s">
          <Button variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
          <Button size="sm" onClick={onConfirm}><Send size={14} /> Submit to AI</Button>
        </div>
      </div>
    </Modal>
  )
}
