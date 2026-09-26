import { LoadError } from '../../shared/ui/ListScaffold'
import { useEffect, useId, useState } from 'react'
import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { api, type ProviderSchema, type ProviderSchemaProp } from '../../shared/data/api'
import { parseJsonField, serializeJsonField } from '../apps/appConfigForm'
import { Button } from '../../shared/ui/Button'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { Toggle } from '../../shared/ui/Toggle'
import { Select, TextArea, TextInput } from '../../shared/ui/forms'
import { SavedToast } from './settingsUI'

export function schemaDefaults(schema: ProviderSchema | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, p] of Object.entries(schema?.properties ?? {})) {
    if (p && p.default !== undefined) out[k] = p.default
  }
  return out
}

export function ProviderConfigForm({ name }: { name: string }) {
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [schema, setSchema] = useState<ProviderSchema | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [secretSet, setSecretSet] = useState<string[]>([])
  const [clearedSecrets, setClearedSecrets] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    setLoadErr(null)
    setSchema(null)
    Promise.all([api.providerSchema(name), api.providerConfig(name)])
      .then(([s, c]) => {
        if (!live) return
        setSchema(s)
        const set = c._secret_set ?? []
        const next = { ...(c.config ?? {}) }
        for (const k of set) next[k] = ''
        setSecretSet(set)
        setClearedSecrets([])
        setValues(next)
      })
      .catch((error) => { if (live) setLoadErr(error) })
    return () => { live = false }
  }, [name, loadAttempt])

  if (loadErr) return <LoadError what="provider configuration" error={loadErr} onRetry={() => setLoadAttempt((n) => n + 1)} />
  if (!schema) return <div data-type="caption" className="py-2 text-on-surface-low"><Loader2 size={12} className="inline animate-spin" /> Loading config…</div>
  const props = Object.entries(schema.properties ?? {})
  if (props.length === 0) return null

  const set = (k: string, v: unknown) => { setValues((p) => ({ ...p, [k]: v })); setDirty(true); setSaved(false); setErr('') }
  const save = async () => {
    setSaving(true); setErr('')
    try {
      const savedConfig = await api.saveProviderConfig(name, Object.fromEntries(Object.entries(values).map(([key, value]) => [key, clearedSecrets.includes(key) ? null : value])))
      setSecretSet(savedConfig._secret_set ?? [])
      setClearedSecrets([])
      setValues(Object.fromEntries(Object.entries(savedConfig.config ?? {}).map(([key, value]) => [key, savedConfig._secret_set?.includes(key) ? '' : value])))
      setDirty(false); setSaved(true); setTimeout(() => setSaved(false), 2000)
    }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Save failed'
      try { const p = JSON.parse(msg); msg = p.error + (p.details ? `: ${p.details.join('; ')}` : '') } catch {   }
      setErr(msg)
    }
    setSaving(false)
  }

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-outline-variant/30 pt-3">
      {props.map(([key, prop]) => (
        <SchemaField key={key} fieldKey={key} prop={prop} value={values[key]}
          secretAlreadySet={secretSet.includes(key) && !clearedSecrets.includes(key)}
          onClear={secretSet.includes(key) ? () => { setClearedSecrets((keys) => [...keys, key]); set(key, '') } : undefined}
          onChange={(v) => { setClearedSecrets((keys) => keys.filter((item) => item !== key)); set(key, v) }} />
      ))}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} loading={saving} disabled={!dirty || saving} disabledReason={!dirty && !saving ? 'No changes to save' : undefined}>Save</Button>
        <SavedToast show={saved} />
        {dirty && !saved && <span data-type="caption" className="text-on-surface-low">Unsaved changes</span>}
        {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    </div>
  )
}

export function SchemaField({ fieldKey, prop, value, onChange, secretAlreadySet = false, onClear }: {
  fieldKey: string; prop: ProviderSchemaProp; value: unknown; onChange: (v: unknown) => void
  secretAlreadySet?: boolean; onClear?: () => void
}) {
  const meta = prop['x-meta'] ?? {}
  const label = meta.label ?? fieldKey
  const [showSecret, setShowSecret] = useState(false)
  const id = useId()
  const [jsonText, setJsonText] = useState(() =>
    serializeJsonField(value, prop.type === 'object' ? 'object' : 'array'))
  const [jsonErr, setJsonErr] = useState<string | null>(null)

  let control: React.ReactNode
  if (prop.type === 'array' || prop.type === 'object') {
    const expected = prop.type === 'object' ? 'object' : 'array'
    control = (
      <TextArea id={id} value={jsonText} rows={4} mono ariaLabel={label} surface="high"
        onChange={(nv) => {
          setJsonText(nv)
          const res = parseJsonField(nv, expected)
          if ('error' in res) { setJsonErr(res.error); return }
          setJsonErr(null)
          onChange(res.value)
        }} />
    )
  } else if (prop.enum && prop.enum.length) {
    control = (
      <Select id={id} value={String(value ?? prop.default ?? '')} onChange={onChange} size="md" surface="high"
        options={prop.enum.map((option) => ({ value: option, label: option }))} />
    )
  } else if (prop.type === 'boolean') {
    const on = Boolean(value ?? prop.default)
    control = <Toggle on={on} onChange={onChange} label={label} />
  } else if (prop.type === 'integer' || prop.type === 'number') {
    control = (
      <TextInput id={id} type="number" value={value == null ? '' : String(value)} min={prop.minimum} max={prop.maximum}
        onChange={(next) => onChange(next === '' ? undefined : Number(next))} size="md" surface="high"
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} />
    )
  } else if (meta.sensitive) {
    control = (
      <TextInput id={id} type={showSecret ? 'text' : 'password'} value={String(value ?? '')} onChange={onChange}
        minLength={prop.minLength} maxLength={prop.maxLength} size="md" surface="high"
        placeholder={secretAlreadySet ? 'saved — leave blank to keep' : meta.placeholder ?? '••••••••'}
        trailingSlot={
          <SquareIconButton label={showSecret ? 'Hide' : 'Show'} onClick={() => setShowSecret((s) => !s)}>
            {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
          </SquareIconButton>
        } />
    )
  } else {
    control = (
      <TextInput id={id} type="text" value={String(value ?? '')} onChange={onChange}
        minLength={prop.minLength} maxLength={prop.maxLength} pattern={prop.pattern}
        placeholder={meta.placeholder ?? (prop.default != null ? String(prop.default) : '')} size="md" surface="high" />
    )
  }

  if (prop.type === 'boolean') {
    return (
      <div className="flex items-center justify-between gap-l">
        <div className="min-w-0">
          <div data-type="body-s" className="text-on-surface">{label}</div>
          {meta.help && <div data-type="caption" className="mt-0.5 text-on-surface-low">{meta.help}</div>}
        </div>
        {control}
      </div>
    )
  }
  const hint = jsonErr ? `${meta.help ? meta.help + ' — ' : ''}⚠ ${jsonErr}` : meta.help
  return (
    <div>
      <label htmlFor={id} data-type="body-s" className="mb-1 block text-on-surface">{label}</label>
      {hint && <div data-type="caption" className="mb-1.5 text-on-surface-low">{hint}</div>}
      {control}
      {meta.sensitive && secretAlreadySet && onClear && <Button variant="ghost" size="sm" onClick={onClear}>Clear saved {label.toLowerCase()}</Button>}
    </div>
  )
}
