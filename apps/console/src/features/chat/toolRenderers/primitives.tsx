import { MoreRow } from '../../../shared/ui/MoreRow'
import { type ReactNode } from 'react'
import { Markdown } from '../../../shared/ui/Markdown'
import { ToolOutput } from '../../tools/ToolOutput'
import type { ToolSegment } from '../chatTypes'

export function resolveInputObj(input: unknown): Record<string, unknown> | null {
  if (input && typeof input === 'object' && !Array.isArray(input)) return input as Record<string, unknown>
  if (typeof input === 'string' && input.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(input.trim())
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>
    } catch {   }
  }
  return null
}

export function inputOf(seg: ToolSegment): Record<string, unknown> | null {
  return resolveInputObj(seg.inputObj) ?? resolveInputObj(seg.input)
}

export function RawBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-1.5 last:mb-0">
      <div data-type="caption" className="mb-0.5 text-on-surface-low uppercase tracking-wide">{label}</div>
      {typeof children === 'string'
        ? <pre data-type="caption" className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-2 py-1.5 font-mono text-on-surface-var leading-relaxed">{children}</pre>
        : <div className="max-h-96 overflow-auto rounded-md bg-surface-low px-2 py-1.5">{children}</div>}
    </div>
  )
}

export function KeyValueFields({ obj, label = 'Input' }: { obj: Record<string, unknown>; label?: string }) {
  const entries = Object.entries(obj)
  if (entries.length === 0) return <RawBlock label={label}>(no arguments)</RawBlock>
  return (
    <div className="mb-1.5">
      <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">{label}</div>
      <div className="flex flex-col gap-1.5 rounded-md bg-surface-low px-2.5 py-s">
        {entries.map(([k, v]) => (
          <div key={k} className="flex flex-col gap-0.5">
            <span data-type="caption" className="font-mono text-on-surface-low uppercase tracking-wide">{k}</span>
            <FieldValue v={v} />
          </div>
        ))}
      </div>
    </div>
  )
}

function FieldValue({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span data-type="caption" className="text-on-surface-low italic">—</span>
  if (typeof v === 'boolean') return <span data-type="caption" style={{ color: v ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>{String(v)}</span>
  if (typeof v === 'number') return <span data-type="caption" className="text-primary-emphasis tabular-nums">{v}</span>
  if (typeof v === 'string') {
    if (v.includes('\n') || v.length > 120) {
      return <pre data-type="caption" className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-surface px-2 py-1 font-mono text-on-surface-var">{v}</pre>
    }
    return <span data-type="body-s" className="text-on-surface break-words">{v}</span>
  }
  return <pre data-type="caption" className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-surface px-2 py-1 font-mono text-on-surface-var">{safeJson(v)}</pre>
}

function safeJson(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

export function ContentTypeOutput({ seg }: { seg: ToolSegment }) {
  const text = seg.output ?? ''
  const ct = seg.contentType
  if (ct === 'diff') {
    return <RawBlock label="Diff"><Markdown>{`\`\`\`diff\n${text}\n\`\`\``}</Markdown></RawBlock>
  }
  if (ct === 'markdown') {
    return <RawBlock label="Result"><div data-type="body-s"><Markdown>{text}</Markdown></div></RawBlock>
  }
  if (ct === 'json') {
    return <RawBlock label="Result"><ToolOutput text={text} /></RawBlock>
  }
  if (ct === 'log' || ct === 'test') {
    return <RawBlock label="Result"><LogView text={text} /></RawBlock>
  }
  if (ct === 'code') {
    return (
      <RawBlock label="Code">
        <pre data-type="caption" className="max-h-72 overflow-auto whitespace-pre-wrap font-mono leading-relaxed text-on-surface-var">{text}</pre>
      </RawBlock>
    )
  }
  if (ct === 'csv') {
    return <RawBlock label="Result"><CsvTable text={text} /></RawBlock>
  }
  return <RawBlock label="Result"><ToolOutput text={text} /></RawBlock>
}

const _ERR_RE = /(error|warn|fail|exception|traceback|fatal|denied|assertionerror|✗|❌|\bE\s)/i
function LogView({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <pre data-type="caption" className="max-h-72 overflow-auto whitespace-pre-wrap font-mono leading-relaxed">
      {lines.map((ln, i) => (
        <div key={i} className={_ERR_RE.test(ln) ? 'text-danger' : 'text-on-surface-var'}>{ln || ' '}</div>
      ))}
    </pre>
  )
}

function CsvTable({ text }: { text: string }) {
  const rows = text.trim().split('\n').filter(Boolean).map((r) => r.split(','))
  if (rows.length < 2) return <pre data-type="caption" className="font-mono whitespace-pre-wrap">{text}</pre>
  const [head, ...body] = rows
  return (
    <div className="overflow-x-auto rounded-md border border-outline-variant/30">
      {
}
      <table className="w-full border-collapse text-[0.75rem]">
        <thead><tr>{head.map((c, i) => <th key={i} className="border-b border-outline-variant/40 bg-surface-high px-2 py-1 text-left font-mono text-on-surface-var">{c}</th>)}</tr></thead>
        <tbody>{body.slice(0, 200).map((r, i) => (
          <tr key={i} className="hover:bg-surface-high/40">{r.map((c, j) => <td key={j} className="border-b border-outline-variant/20 px-2 py-1 align-top">{c}</td>)}</tr>
        ))}</tbody>
      </table>
      {
}
      <MoreRow total={body.length} shown={200} noun="rows" className="px-2 py-1" />
    </div>
  )
}
