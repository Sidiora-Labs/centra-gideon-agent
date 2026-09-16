import { useState } from 'react'
import { Field, Select, TextArea } from '../../shared/ui/forms'
import { api } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { missingRequired } from '../tools/schema'

export function serializeJsonField(value: unknown, expected: 'array' | 'object'): string {
  if (value === undefined || value === null) return expected === 'array' ? '[]' : '{}'
  try { return JSON.stringify(value, null, 2) } catch { return '' }
}

export function parseJsonField(text: string, expected: 'array' | 'object'):
  { value: unknown } | { error: string } {
  const trimmed = text.trim()
  if (trimmed === '') return { value: expected === 'array' ? [] : {} }
  let parsed: unknown
  try { parsed = JSON.parse(trimmed) } catch { return { error: 'invalid JSON' } }
  const okType = expected === 'array' ? Array.isArray(parsed)
    : (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed))
  if (!okType) return { error: `must be a JSON ${expected}` }
  return { value: parsed }
}

function JsonField({ label, help, expected, value, onChange }: {
  label: string
  help?: string
  expected: 'array' | 'object'
  value: unknown
  onChange: (v: unknown) => void
}) {
  const [text, setText] = useState(() => serializeJsonField(value, expected))
  const [error, setError] = useState<string | null>(null)
  const hint = error ? `${help ? help + ' — ' : ''}⚠ ${error}` : help
  return (
    <Field label={label} hint={hint}>
      <TextArea
        value={text}
        rows={4}
        mono
        ariaLabel={label}
        onChange={(nv) => {
          setText(nv)
          const res = parseJsonField(nv, expected)
          if ('error' in res) { setError(res.error); return }
          setError(null)
          onChange(res.value)
        }}
      />
    </Field>
  )
}

export interface SchemaProp {
  type?: string
  default?: unknown
  enum?: unknown[]
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  'x-meta'?: { label?: string; help?: string; sensitive?: boolean }
}

export interface AppConfigSchema {
  properties?: Record<string, SchemaProp>
  required?: string[]
}

export function AppConfigFields({ appName, props, cur, set, secretSet = [], required = [] }: {
  appName: string
  props: Record<string, SchemaProp>
  cur: Record<string, unknown>
  set: (key: string, value: unknown) => void
  required?: readonly string[]
  secretSet?: string[]
}) {
  return (
    <>
      {Object.entries(props).map(([key, p]) => {
        const meta = p['x-meta'] ?? {}
        const isRequired = required.includes(key)
        const label = (meta.label || key) + (isRequired ? ' *' : '')
        const v = cur[key]
        const fieldId = `app-cfg-${appName}-${key}`
        const secretAlreadySet = !!meta.sensitive && secretSet.includes(key)
        if (Array.isArray(p.enum) && p.enum.length) {
          return (
            <Field key={key} label={label} hint={meta.help}>
              <Select name={fieldId} value={String(v ?? '')} onChange={(nv) => set(key, nv)}
                required={isRequired}
                options={p.enum.map((o) => ({ value: String(o), label: String(o) }))} />
            </Field>
          )
        }
        if (p.type === 'boolean') {
          return (
            <Field key={key} label={label} hint={meta.help}>
              <button type="button" id={fieldId} name={fieldId} onClick={() => set(key, !v)}
                className={`h-6 w-11 rounded-pill transition-colors ${v ? 'bg-primary' : 'bg-surface-highest'}`}
                aria-pressed={!!v} aria-label={label} aria-required={isRequired || undefined}>
                <span className={`block size-5 rounded-full bg-white transition-transform ${v ? 'translate-x-5' : 'translate-x-0.5'}`} />
              </button>
            </Field>
          )
        }
        if (p.type === 'array' || p.type === 'object') {
          return (
            <JsonField key={key} label={label} help={meta.help}
              expected={p.type} value={v} onChange={(nv) => set(key, nv)} />
          )
        }
        const isNum = p.type === 'integer' || p.type === 'number'
        return (
          <Field key={key} label={label} hint={meta.help}>
            <input
              id={fieldId} name={fieldId}
              aria-required={isRequired || undefined}
              type={meta.sensitive ? 'password' : isNum ? 'number' : 'text'}
              placeholder={secretAlreadySet ? 'saved — leave blank to keep' : undefined}
              min={isNum && typeof p.minimum === 'number' ? p.minimum : undefined}
              max={isNum && typeof p.maximum === 'number' ? p.maximum : undefined}
              step={p.type === 'integer' ? 1 : undefined}
              minLength={!isNum && typeof p.minLength === 'number' ? p.minLength : undefined}
              maxLength={!isNum && typeof p.maxLength === 'number' ? p.maxLength : undefined}
              pattern={!isNum && !meta.sensitive && typeof p.pattern === 'string' ? p.pattern : undefined}
              className="w-full rounded-md border border-outline-variant bg-surface-high px-m py-s text-[0.8125rem] text-on-surface"
              value={v === undefined || v === null ? '' : String(v)}
              onChange={(e) => {
                const raw = e.target.value
                set(key, isNum ? (raw === '' ? undefined : Number(raw)) : raw)
              }} />
          </Field>
        )
      })}
    </>
  )
}

export function useAppConfig(name: string) {
  const { data, error: loadErr, refresh } = useQuery(`app-config:${name}`, () => api.appConfig(name), { persist: false })
  const [values, setValues] = useState<Record<string, unknown> | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState(0)
  const reload = () => { invalidateKeys(`app-config:${name}`); refresh() }

  const schema = (data?.schema ?? {}) as AppConfigSchema
  const props = schema.properties ?? {}
  const hasSchema = Object.keys(props).length > 0
  const secretSet = data?._secret_set ?? []
  const required = (schema.required ?? []).filter((k) => k in props)
  const cur: Record<string, unknown> = values ?? (() => {
    const base: Record<string, unknown> = {}
    for (const [k, p] of Object.entries(props)) if (p.default !== undefined) base[k] = p.default
    const merged = { ...base, ...(data?.config ?? {}) }
    for (const k of secretSet) merged[k] = ''
    return merged
  })()

  const set = (k: string, v: unknown) => setValues({ ...cur, [k]: v })
  const dirty = values !== null
  const missing = missingRequired(cur, required, { satisfied: secretSet })
  const missingLabels = missing.map((k) => props[k]?.['x-meta']?.label || k)

  async function save(onDone?: () => void) {
    if (data === undefined) {
      setErr(loadErr
        ? "Couldn't load this app's configuration, so there is nothing to save yet. Retry the load first."
        : 'Still loading this app’s configuration — nothing to save yet.')
      return
    }
    if (missing.length > 0) {
      setErr(`Fill in ${missingLabels.join(', ')} before saving.`)
      return
    }
    setBusy(true); setErr(null)
    try {
      await api.saveAppConfig(name, cur)
      invalidateKeys(`app-config:${name}`)
      setValues(null)
      setSavedAt(Date.now())
      onDone?.()
    } catch (e) { setErr(String((e as Error).message || e)) }
    finally { setBusy(false) }
  }

  return { loading: data === undefined && !loadErr, error: loadErr, reload,
    props, hasSchema, cur, set, save, busy, err, dirty, savedAt, secretSet,
    required, missing, missingLabels }
}
