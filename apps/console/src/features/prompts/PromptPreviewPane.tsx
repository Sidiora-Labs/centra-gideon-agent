import { useTemplatePreview } from './promptEditorState'
import { Eye, AlertTriangle, Loader2, Puzzle } from 'lucide-react'
import { type PromptVariable } from '../../shared/data/api'
import type { PromptDraft } from './PromptForm'

export function PromptPreviewPane({ draft }: { draft: PromptDraft }) {
  const { vars, values, rendered, error, includes, busy, setVal } = useTemplatePreview(draft)

  return (
    <div className="grid h-full content-start gap-m">
      <div className="flex items-center gap-1.5 text-on-surface-var">
        <Eye size={14} /> <span data-type="title-s">Live preview</span>
        {busy && <Loader2 size={13} className="animate-spin text-on-surface-low" />}
      </div>

      {vars.length > 0 && (
        <div className="flex flex-col gap-2 rounded-md border border-outline-variant/30 bg-surface-container/40 p-2.5">
          <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Sample values</div>
          {vars.map((v) => (
            <SampleField key={v.name} v={v} value={values[v.name]} onChange={(x) => setVal(v.name, x)} />
          ))}
        </div>
      )}

      {includes.length > 0 && (
        <div data-type="caption" className="flex flex-wrap items-center gap-1.5 text-on-surface-low">
          <Puzzle size={12} /> includes:
          {includes.map((n) => <code key={n} className="rounded bg-surface-high px-1 font-mono">{n}</code>)}
        </div>
      )}

      {error ? (
        <div role="alert" data-type="body-s" className="flex items-start gap-2 rounded-lg px-3 py-2"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>Couldn't render this template: {error}</span>
        </div>
      ) : (
        <pre data-type="body-s" className="min-h-[120px] flex-1 overflow-auto whitespace-pre-wrap rounded-md border border-outline-variant/30 bg-surface-container/40 p-3 font-mono leading-relaxed text-on-surface">
          {rendered || <span className="text-on-surface-low">Type a template to see the assembled output.</span>}
        </pre>
      )}
    </div>
  )
}

function SampleField({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (value: unknown) => void }) {
  const base = 'w-full rounded-md border border-outline-variant/25 bg-surface px-m text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  const text = value == null ? '' : String(value)
  const label = <span data-type="caption" className="inline-flex items-center gap-1 text-on-surface-var"><code className="font-mono">{v.name}</code>{v.required && <span className="text-danger">*</span>}</span>
  if (v.type === 'boolean') return <div className="flex items-center justify-between gap-s">{label}<button type="button" aria-label={v.name} aria-pressed={Boolean(value)} onClick={() => onChange(!value)} data-type="caption" className="min-h-7 rounded-md border border-outline-variant/30 px-m" style={{ background: value ? 'var(--color-primary)' : 'var(--color-surface-high)', color: value ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}>{value ? 'true' : 'false'}</button></div>
  const control = v.type === 'select'
    ? <select value={text} onChange={event => onChange(event.target.value)} aria-label={v.name} data-type="body-s" className={`${base} h-8`}><option value="">—</option>{(v.options ?? []).map(option => <option key={option} value={option}>{option}</option>)}</select>
    : v.type === 'textarea'
      ? <textarea value={text} onChange={event => onChange(event.target.value)} rows={2} aria-label={v.name} data-type="body-s" className={`${base} resize-y py-s`} />
      : <input type={v.type === 'number' ? 'number' : 'text'} value={text} onChange={event => onChange(event.target.value)} aria-label={v.name} data-type="body-s" className={`${base} h-8`} />
  return <div className="grid gap-1">{label}{control}</div>
}
