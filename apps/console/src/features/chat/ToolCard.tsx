import { useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { ChevronRight, Loader2, Check, Zap, Maximize2, Lightbulb, AlertTriangle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { spring } from '../../shared/theme/motion'
import type { ToolSegment } from './chatTypes'
import { renderToolInput, renderToolOutput, iconForTool, labelForTool, inputOf } from './toolRenderers/registry'
import { requestToolResultFull } from './toolResultBridge'
import { ToolCall } from '../../shared/vendor/assistant-ui/elements/tool-call'
import { ToolError } from '../../shared/vendor/assistant-ui/elements/tool-error'
import { ToolFallbackRoot, ToolFallbackTrigger, ToolFallbackContent } from '../../shared/vendor/assistant-ui/elements/tool-fallback.aui'
import { nativeRendererForTool } from './toolRenderers/native'
import { sniffContentType } from './toolRenderers/registry'

export function ToolCard({ seg, connected = false }: { seg: ToolSegment; connected?: boolean }) {
  const [open, setOpen] = useState(false)
  const Icon = iconForTool(seg)
  const label = labelForTool(seg)
  const detail = secondaryDetail(seg)
  const failed = seg.ok === false || !!seg.agentError
  const status = seg.done ? (failed ? 'failed' : 'completed') : 'running'
  const srLabel = `Tool ${label}${detail ? ` ${detail}` : ''} — ${status}${seg.auto ? ', auto-approved' : ''}. ${open ? 'Collapse' : 'Expand'} details`

  if (detail && !nativeRendererForTool(seg.tool) && !seg.truncated && !seg.agentError && !seg.recoveryHints?.length
    && !seg.auto && !seg.purpose && !seg.rawRef && !seg.inputObj
    && seg.ok !== false && (seg.contentType ?? sniffContentType(seg.output)) === 'generic') {
    return <ToolCall label={label} activeLabel={`Running ${label}`} query={detail}
      request={seg.input ?? ''} result={seg.output ?? ''} running={!seg.done}
      open={open} onOpenChange={setOpen} className="my-1 max-w-none" />
  }

  if (connected) return (
    <ToolFallbackRoot open={open} onOpenChange={setOpen} className="my-1 border border-outline-variant/40 bg-surface-low/40">
      <div className="flex min-w-0 items-center gap-2 px-3">
        <ToolFallbackTrigger toolName={label} aria-label={srLabel}
          status={seg.done ? failed ? { type: 'incomplete', reason: 'error' } : { type: 'complete' } : { type: 'running' }} />
        {detail && <span data-type="caption" className="min-w-0 flex-1 truncate font-mono text-on-surface-low">{detail}</span>}
        {seg.auto && <Zap size={11} className="shrink-0 text-on-surface-low" aria-label="Auto-approved" />}
      </div>
      <ToolFallbackContent><ToolCardDetails seg={seg} /></ToolFallbackContent>
    </ToolFallbackRoot>
  )

  return (
    <div className="my-1 overflow-hidden border border-outline-variant/40 bg-surface-low/40"
      style={{ borderRadius: 'var(--radius-md)' }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
        aria-expanded={open} aria-label={srLabel}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-low/70">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 text-on-surface-low">
          <ChevronRight size={13} />
        </motion.span>
        <Icon size={14} className="shrink-0 text-primary" />
        <span data-type="label-s" className="shrink-0 text-on-surface" style={fvs(550)}>{label}</span>
        {detail && (
          <>
            <span data-type="body-s" className="shrink-0 text-on-surface-low/50">·</span>
            <span data-type="caption" className="min-w-0 flex-1 truncate font-mono text-on-surface-low">{detail}</span>
          </>
        )}
        <span className={detail ? 'shrink-0' : 'flex-1'} />
        {seg.auto && <Zap size={11} className="shrink-0 text-on-surface-low" aria-hidden />}
        {seg.done
          ? failed
            ? <AlertTriangle size={14} className="shrink-0" aria-hidden style={{ color: 'var(--color-danger)' }} />
            : <Check size={14} className="shrink-0" aria-hidden style={{ color: 'var(--color-ok)' }} />
          : <Loader2 size={13} className="shrink-0 animate-spin text-on-surface-low" aria-hidden />}
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            transition={{ height: spring.spatialFast, opacity: { duration: 0.15 } }}>
            <ToolCardDetails seg={seg} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ToolCardDetails({ seg }: { seg: ToolSegment }) {
  const donorError = seg.done && seg.agentError?.what ? seg.agentError.what : null
  return (
    <div className="border-t border-outline-variant/30 px-3 py-2">
      {seg.purpose && <p data-type="caption" className="mb-1.5 text-on-surface-var">{seg.purpose}</p>}
      {renderToolInput(seg)}
      {renderToolOutput(seg)}
      {donorError && <ToolError name={labelForTool(seg)} target={secondaryDetail(seg) || seg.tool} message={donorError} />}
      {seg.done && (seg.output == null || seg.output === '') && (
        <p data-type="caption" className="text-on-surface-low">No output.</p>
      )}
      {seg.truncated && (
        <div data-type="caption" className="mt-1.5 flex items-center gap-2 text-on-surface-low">
          <span>
            showing a projection{seg.originalLength ? ` of ${seg.originalLength.toLocaleString()} chars` : ''}
          </span>
          {seg.rawRef && (
            <button type="button" onClick={() => requestToolResultFull(seg.rawRef!, seg.tool)}
              className="inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var transition-colors hover:bg-surface-highest hover:text-on-surface">
              <Maximize2 size={11} /> Show full result
            </button>
          )}
        </div>
      )}
      {seg.agentError && (
        <div className="mt-1.5 rounded-md border border-danger/30 bg-danger/5 px-2.5 py-1.5">
          <div data-type="caption" className="mb-1 flex items-center gap-1 text-on-surface-low uppercase tracking-wide">
            <AlertTriangle size={11} style={{ color: 'var(--color-danger)' }} />
            <span className="font-mono">{seg.agentError.code}</span>
          </div>
          <dl data-type="caption" className="flex flex-col gap-0.5">
            {!donorError && <div className="flex gap-1.5">
              <dt className="shrink-0 text-on-surface-low" style={fvs(600)}>What</dt>
              <dd className="min-w-0 text-on-surface-var">{seg.agentError.what}</dd>
            </div>}
            <div className="flex gap-1.5">
              <dt className="shrink-0 text-on-surface-low" style={fvs(600)}>Why</dt>
              <dd className="min-w-0 text-on-surface-var">{seg.agentError.why}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="shrink-0 text-on-surface-low" style={fvs(600)}>Fix</dt>
              <dd className="min-w-0 text-on-surface-var">{seg.agentError.fix}</dd>
            </div>
          </dl>
          {seg.agentError.suggestions && seg.agentError.suggestions.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <span data-type="caption" className="text-on-surface-low uppercase tracking-wide">Did you mean</span>
              {seg.agentError.suggestions.map((s, i) => (
                <span key={i} data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-var">{s}</span>
              ))}
            </div>
          )}
        </div>
      )}
      {seg.recoveryHints && seg.recoveryHints.length > 0 && (
        <div className="mt-1.5 rounded-md bg-surface-container px-2.5 py-1.5">
          <div data-type="caption" className="mb-0.5 flex items-center gap-1 text-on-surface-low uppercase tracking-wide">
            <Lightbulb size={11} /> Next steps
          </div>
          <ul className="flex flex-col gap-0.5">
            {seg.recoveryHints.map((h, i) => (
              <li key={i} data-type="caption" className="text-on-surface-var">{h}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function secondaryDetail(seg: ToolSegment): string {
  if (seg.detail) return seg.detail.replace(/\s+/g, ' ').trim()
  const obj = inputOf(seg)
  if (obj) return summarizeInputObj(obj)
  const inp = (seg.input ?? '').trim()
  if (!inp || inp.startsWith('{') || inp.startsWith('[')) return ''
  const flat = inp.replace(/\s+/g, ' ').trim()
  return flat.length <= 80 ? flat : ''
}

function summarizeInputObj(obj: Record<string, unknown>): string {
  const entries = Object.entries(obj).filter(([, v]) => v != null && v !== '')
  const scalars = entries.filter(([, v]) => typeof v !== 'object')
  if (scalars.length === 1) {
    const s = String(scalars[0][1]).replace(/\s+/g, ' ').trim()
    return s.length <= 80 ? s : s.slice(0, 77) + '…'
  }
  if (scalars.length >= 2 && scalars.length <= 4) {
    const parts = scalars.map(([k, v]) => `${k}=${String(v).replace(/\s+/g, ' ').trim()}`)
    const joined = parts.join(' · ')
    if (joined.length <= 80) return joined
    const first = String(scalars[0][1]).replace(/\s+/g, ' ').trim()
    return first.length <= 80 ? first : first.slice(0, 77) + '…'
  }
  return ''
}
