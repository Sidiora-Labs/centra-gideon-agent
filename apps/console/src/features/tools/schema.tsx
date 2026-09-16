import { useId, useState, type ReactNode } from 'react'
import { Toggle } from '../../shared/ui/Toggle'


export interface SchemaMeta {
  label?: string
  help?: string
  widget?: string
  tags?: string[]
}

export interface JsonSchema {
  type?: string | string[]
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  enum?: unknown[]
  default?: unknown
  'x-meta'?: SchemaMeta
}

export function schemaMeta(s: JsonSchema): SchemaMeta {
  return (s['x-meta'] ?? {}) as SchemaMeta
}

export function schemaProps(parameters: unknown): { props: [string, JsonSchema][]; required: Set<string> } {
  const s = (parameters ?? {}) as JsonSchema
  const props = Object.entries(s.properties ?? {})
  return { props, required: new Set(s.required ?? []) }
}

export function missingRequired(
  values: Record<string, unknown>,
  required: readonly string[] | ReadonlySet<string>,
  opts?: { satisfied?: readonly string[] },
): string[] {
  const satisfied = new Set(opts?.satisfied ?? [])
  const out: string[] = []
  for (const key of required) {
    if (satisfied.has(key)) continue
    const v = values[key]
    if (v === undefined || v === null) { out.push(key); continue }
    if (typeof v === 'string' && v.trim() === '') out.push(key)
  }
  return out
}

export function typeLabel(s: JsonSchema): string {
  const t = Array.isArray(s.type) ? s.type.join('|') : s.type
  if (t === 'array') return `${(s.items?.type as string) ?? 'any'}[]`
  if (s.enum) return 'enum'
  return t ?? 'any'
}

export function seedArgs(parameters: unknown): Record<string, unknown> {
  const { props, required } = schemaProps(parameters)
  const out: Record<string, unknown> = {}
  for (const [k, s] of props) {
    if (s.default !== undefined) out[k] = s.default
    else if (s.enum?.length) out[k] = ''
    else if (s.type === 'boolean') out[k] = required.has(k) ? false : ''
    else out[k] = ''
  }
  return out
}

export type WidgetRenderer = (props: {
  value: unknown; onChange: (v: unknown) => void; schema: JsonSchema; placeholder?: string
}) => ReactNode
export type WidgetMap = Record<string, WidgetRenderer>

export function SchemaField({ name, schema, required, value, onChange, widgets }: {
  name: string; schema: JsonSchema; required: boolean; value: unknown; onChange: (v: unknown) => void
  widgets?: WidgetMap
}) {
  const t = Array.isArray(schema.type) ? schema.type[0] : schema.type
  const meta = schemaMeta(schema)
  const label = meta.label ?? name
  const id = useId()
  const base = 'w-full rounded-md bg-surface px-m py-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  let control: ReactNode
  const customWidget = meta.widget ? widgets?.[meta.widget] : undefined
  if (customWidget) {
    control = <div role="group" aria-labelledby={`${id}-label`}>{customWidget({ value, onChange, schema, placeholder: meta.help })}</div>
  } else if (schema.enum?.length) {
    control = (
      <select id={id} data-type="body-s" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} className={`${base}`}>
        <option value="">—</option>
        {schema.enum.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    )
  } else if (t === 'boolean') {
    control = <Toggle on={!!value} onChange={onChange} size="sm" label={label} />
  } else if (t === 'number' || t === 'integer') {
    control = <input id={id} data-type="body-s" type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} className={base} />
  } else if (t === 'object' || t === 'array') {
    control = <textarea id={id} data-type="caption" value={typeof value === 'string' ? value : JSON.stringify(value ?? (t === 'array' ? [] : {}), null, 2)} onChange={(e) => onChange(e.target.value)} rows={3} placeholder={t === 'array' ? '[ … ]' : '{ … }'} className={`${base} font-mono resize-y`} />
  } else {
    control = <input id={id} data-type="body-s" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} placeholder={meta.help?.slice(0, 60) ?? schema.description?.slice(0, 60)} className={base} />
  }
  const bindsHtmlFor = !customWidget && t !== 'boolean'
  return (
    <div>
      <div className="mb-1 flex items-center gap-s">
        <label id={`${id}-label`} htmlFor={bindsHtmlFor ? id : undefined} data-type="body-s" className="text-on-surface">{label}</label>
        <span data-type="caption" className="text-on-surface-low font-mono">{typeLabel(schema)}</span>
        {required && <span data-type="caption" className="text-danger">required</span>}
      </div>
      {control}
      {meta.help && <p data-type="caption" className="mt-1 text-on-surface-low">{meta.help}</p>}
    </div>
  )
}

export function buildArgs(parameters: unknown, raw: Record<string, unknown>): { args: Record<string, unknown>; error?: string } {
  const { props, required } = schemaProps(parameters)
  const args: Record<string, unknown> = {}
  for (const [k, s] of props) {
    const v = raw[k]
    const t = Array.isArray(s.type) ? s.type[0] : s.type
    if ((v === '' || v == null) && !required.has(k)) continue
    if (t === 'object' || t === 'array') {
      if (typeof v === 'string' && v.trim()) {
        try { args[k] = JSON.parse(v) } catch { return { args, error: `${k}: invalid JSON` } }
      } else if (typeof v !== 'string') args[k] = v
    } else {
      args[k] = v
    }
  }
  return { args }
}

export function useArgs(parameters: unknown) {
  return useState<Record<string, unknown>>(() => seedArgs(parameters))
}
