import { useState } from 'react'
import { MoreRow } from '../../shared/ui/MoreRow'
import {
  Plus, Cpu, Wifi, Pencil, Trash2, X, Eye, EyeOff,
  CheckCircle2, AlertTriangle, ChevronRight, RotateCcw,
} from 'lucide-react'
import { api, type ModelProvider, type AvailableModel, type ProviderTestResult, type SchemaProp } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { confirmDelete } from '../../shared/ui/dialog'
import { Button } from '../../shared/ui/Button'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { Skeleton, LoadingStatus } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { TextInput } from '../../shared/ui/forms'
import { OllamaModelManager } from './OllamaModelManager'
import { fvs } from '../../shared/theme/fontWeight'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { SchemaFieldDisclosure } from '../tools/schema'

const typeLabel = (type: string) => type

function RemoteProvidersSkeleton() {
  return (
    <div className="mb-3 flex flex-col gap-2" role="status" aria-busy="true" >
        <LoadingStatus what="model providers" />
      {Array.from({ length: 2 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-lg bg-surface-container px-l py-m">
          <Skeleton className="size-7 shrink-0 rounded-lg" />
          <div className="flex-1 min-w-0 space-y-2"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div>
          <Skeleton className="h-5 w-20 shrink-0 rounded-pill" />
        </div>
      ))}
    </div>
  )
}

export function RemoteModelProviders() {
  const [adding, setAdding] = useState(false)
  const { data, error, refresh } = useQuery('settings:remote-model-providers', async () => {
    const [provs, rows] = await Promise.all([
      api.modelProviders(),
      api.modelsAvailable().catch(() => [] as { name: string; models?: AvailableModel[] }[]),
    ])
    const map: Record<string, AvailableModel[]> = {}
    for (const r of rows) map[r.name] = [...(map[r.name] ?? []), ...(r.models ?? [])]
    return { providers: provs, available: map }
  }, { persist: true })
  const reload = () => { invalidateKeys('settings:remote-model-providers'); refresh() }
  const available = data?.available ?? {}

  if (!data?.providers && error) return (
    <InlineError icon className="mb-3">
      <span className="flex-1">Couldn't load your remote model providers{(error as Error)?.message ? `: ${(error as Error).message}` : '.'}</span>
      <Button variant="secondary" size="sm" onClick={reload}><RotateCcw size={14} /> Retry</Button>
    </InlineError>
  )
  if (!data?.providers) return <RemoteProvidersSkeleton />
  const providers = data.providers.filter((p) => p.type !== 'ollama')
  return (
    <div>
      {providers.length === 0 ? (
        <p data-type="body-s" className="mb-3 text-on-surface-low">No remote model providers yet. Add an instance to contribute models to the pool.</p>
      ) : (
        <div className="mb-3 flex flex-col gap-2">
          {providers.map((p) => (
            <InstanceCard key={p.name} provider={p} models={available[p.name] ?? []} onChanged={reload} />
          ))}
        </div>
      )}

      {adding
        ? <AddInstanceForm onDone={(created) => { setAdding(false); if (created) reload() }} />
        : <Button variant="secondary" size="sm" onClick={() => setAdding(true)}><Plus size={15} /> Add instance</Button>}
    </div>
  )
}

function CredBadge({ status }: { status: string }) {
  const ok = status === 'ok'
  const missing = status === 'missing'
  const color = ok ? 'var(--color-success)' : missing ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  return (
    <span data-type="caption" className="inline-flex shrink-0 items-center gap-1" style={{ color }}>
      {ok ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />} {ok ? 'Configured' : missing ? 'Missing key' : 'Unconfigured'}
    </span>
  )
}

function InstanceCard({ provider, models, onChanged }: { provider: ModelProvider; models: AvailableModel[]; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [showModels, setShowModels] = useState(false)
  const [test, setTest] = useState<ProviderTestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [busy, setBusy] = useState(false)

  const runTest = async () => {
    setTesting(true); setTest(null)
    try { setTest(await api.testModelProvider(provider.name)) }
    catch (e) { setTest({ ok: false, message: e instanceof Error ? e.message : 'Test failed' }) }
    setTesting(false)
  }
  const remove = async () => {
    const selections = ' Any use case set to one of its models loses that selection.'
    const key = provider.credential_status === 'ok' ? ' Its saved credential stays in the store.' : ''
    if (!(await confirmDelete('provider', provider.name, {
      body: `Models it provides will no longer be available.${selections}${key}`,
    }))) return
    setBusy(true)
    if (!(await reportingWrite(`remove ${provider.name}`, () => api.deleteModelProvider(provider.name)))) {
      setBusy(false)
      return
    }
    onChanged()
  }

  return (
    <div className="rounded-lg bg-surface-container px-4 py-3" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-3">
        <Cpu size={17} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span data-type="title-m" className="truncate text-on-surface" style={fvs(500)}>{provider.name}</span>
            <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 py-0.5 text-on-surface-low">{typeLabel(provider.type)}</span>
          </div>
          {provider.capabilities.length > 0 && (
            <div data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-x-2 text-on-surface-low">
              {provider.capabilities.map((c) => <span key={c}>{c}</span>)}
            </div>
          )}
        </div>
        <CredBadge status={provider.credential_status} />
        <div className="flex shrink-0 items-center gap-0.5">
          {
}
          <SquareIconButton label="Test connection" onClick={runTest} loading={testing}><Wifi size={14} /></SquareIconButton>
          {
}
          <SquareIconButton label={provider.type === 'ollama' ? 'Manage models' : 'View models'} onClick={() => setShowModels((v) => !v)} ariaExpanded={showModels}>
            <ChevronRight size={14} style={{ transform: showModels ? 'rotate(90deg)' : 'none' }} />
          </SquareIconButton>
          <SquareIconButton label="Edit" onClick={() => setEditing((v) => !v)} ariaExpanded={editing}>{editing ? <X size={14} /> : <Pencil size={14} />}</SquareIconButton>
          <SquareIconButton label="Delete" onClick={remove}><Trash2 size={14} /></SquareIconButton>
        </div>
      </div>

      {test && (
        <div data-type="caption" className="mt-2 flex items-center gap-1.5" style={{ color: test.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
          {test.ok ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />} {test.message}
        </div>
      )}

      {showModels && (
        provider.type === 'ollama' ? (
          <OllamaModelManager provider={provider.name} />
        ) : (
          <div className="mt-3 border-t border-outline-variant/30 pt-3">
            {models.length === 0 ? (
              <p data-type="caption" className="text-on-surface-low italic">No models discovered — test the connection or check the endpoint.</p>
            ) : (
              <>
                <div data-type="caption" className="mb-1.5 text-on-surface-low uppercase tracking-wide">Available models ({models.length})</div>
                <div className="flex flex-wrap gap-1">
                  {models.slice(0, 24).map((m) => <span key={m.id} data-type="caption" className="rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface font-mono">{m.name}</span>)}
                  <MoreRow total={models.length} shown={24} className="px-1" />
                </div>
              </>
            )}
          </div>
        )
      )}

      {editing && <EditInstanceForm provider={provider} onDone={(saved) => { setEditing(false); if (saved) onChanged() }} />}
    </div>
  )
}

const inputCls = 'h-9 w-full rounded-md bg-surface-high px-3 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'

export function SchemaField({ field, name, value, onChange }: {
  field: SchemaProp; name: string; value: string; onChange: (v: string) => void
}) {
  const [show, setShow] = useState(false)
  const meta = field['x-meta'] || {}
  const label = meta.label || name
  const enumVals = field.enum
  if (Array.isArray(enumVals) && enumVals.length > 0) {
    return (
      <label className="flex flex-col gap-1">
        <span data-type="caption" className="text-on-surface-low">{label}</span>
        <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} data-type="body-s" className={inputCls + ' cursor-pointer'}>
          {enumVals.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
        </select>
        {meta.help && <span data-type="caption" className="text-on-surface-low">{meta.help}</span>}
      </label>
    )
  }
  const sensitive = !!meta.sensitive
  return (
    <label className="flex flex-col gap-1">
      <span data-type="caption" className="text-on-surface-low">{label}</span>
      <div className="relative">
        <input aria-label={label} type={sensitive && !show ? 'password' : 'text'} value={value}
          onChange={(e) => onChange(e.target.value)} placeholder={meta.help || label}
          data-type="body-s" className={inputCls + (sensitive ? ' pr-10' : '')} />
        {sensitive && (
          <span className="absolute right-1.5 top-1/2 -translate-y-1/2">
            <SquareIconButton label={show ? 'Hide' : 'Show'} onClick={() => setShow((s) => !s)}>
              {show ? <EyeOff size={14} /> : <Eye size={14} />}
            </SquareIconButton>
          </span>
        )}
      </div>
      {meta.help && !sensitive && <span data-type="caption" className="text-on-surface-low">{meta.help}</span>}
    </label>
  )
}

function AddInstanceForm({ onDone }: { onDone: (created: boolean) => void }) {
  const { data: types } = useQuery('settings:model-provider-types', () => api.modelProviderTypes(), { persist: true })
  const [typeIdx, setTypeIdx] = useState(0)
  const [name, setName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const selected = types && types.length > 0 ? types[Math.min(typeIdx, types.length - 1)] : null
  const props = selected?.settingsSchema?.properties || {}
  const required = selected?.settingsSchema?.required || []
  const seedFor = (t: typeof selected) => {
    const seed: Record<string, string> = {}
    for (const [k, f] of Object.entries(t?.settingsSchema?.properties || {})) seed[k] = String(f.default ?? '')
    return seed
  }

  if (!types) {
    return <div data-type="body-s" className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low">Loading provider types…</div>
  }
  if (types.length === 0) {
    return (
      <div data-type="body-s" className="rounded-lg border border-outline-variant/40 bg-surface p-4 text-on-surface-low">
        No model-provider apps installed. Install one from the Store (e.g. OpenAI, Anthropic, Amazon Bedrock) to add an instance.
      </div>
    )
  }

  const submit = async () => {
    if (!selected) return
    if (!name.trim()) { setError('Instance name is required'); return }
    for (const r of required) {
      if (!String(values[r] ?? props[r]?.default ?? '').trim()) {
        setError(`${props[r]?.['x-meta']?.label || r} is required`); return
      }
    }
    setSaving(true); setError('')
    const options: Record<string, string> = {}
    for (const [k, f] of Object.entries(props)) {
      const v = (values[k] ?? String(f.default ?? '')).trim()
      if (v) options[k] = v
    }
    try { await api.createModelProvider({ name: name.trim(), type: selected.type, model: '', options }); onDone(true) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Failed to add instance'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
      setError(msg); setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface p-4">
      <div data-type="label-s" className="mb-3 text-on-surface" style={fvs(600)}>Add model provider instance</div>
      <div className="grid grid-cols-2 gap-2">
        <select aria-label="Provider type" value={typeIdx}
          onChange={(e) => { const i = Number(e.target.value); setTypeIdx(i); setValues(seedFor(types[i])); setError('') }}
          data-type="body-s" className={inputCls + ' cursor-pointer'}>
          {types.map((t, i) => <option key={t.type} value={i}>{t.label}</option>)}
        </select>
        <TextInput ariaLabel="Instance name" value={name} onChange={setName} placeholder="Instance name (e.g. my-bedrock)" size="md" surface="high" />
      </div>
      <div className="mt-2 flex flex-col gap-2">
        <SchemaFieldDisclosure fields={Object.entries(props)} required={required} values={values}
          renderField={([k, f]) => <SchemaField key={k} name={k} field={f}
            value={values[k] ?? String(f.default ?? '')}
            onChange={(v) => setValues((prev) => ({ ...prev, [k]: v }))} />} />
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" onClick={submit} loading={saving} loadingLabel="Adding…">Add instance</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}

function EditInstanceForm({ provider, onDone }: { provider: ModelProvider; onDone: (saved: boolean) => void }) {
  const isAws = provider.type === 'bedrock'
  const [endpoint, setEndpoint] = useState('')
  const [region, setRegion] = useState('')
  const [profile, setProfile] = useState('')
  const [model, setModel] = useState(provider.model ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const save = async () => {
    setSaving(true); setError('')
    const body: { model?: string; options?: Record<string, string> } = {}
    const options: Record<string, string> = {}
    if (isAws) {
      if (region.trim()) options.region = region.trim()
      if (profile.trim()) options.profile = profile.trim()
    } else if (endpoint.trim()) {
      options.endpoint = endpoint.trim()
    }
    if (Object.keys(options).length) body.options = options
    if (model.trim() !== (provider.model ?? '')) body.model = model.trim()
    if (!body.options && body.model === undefined) { onDone(false); return }
    try { await api.updateModelProvider(provider.name, body); onDone(true) }
    catch (e) { setError(e instanceof Error ? e.message : 'Save failed'); setSaving(false) }
  }

  return (
    <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant/30 pt-3">
      {isAws ? (
        <>
          <TextInput ariaLabel="AWS region" value={region} onChange={setRegion} placeholder="AWS region (leave empty to keep current)" size="md" surface="high" />
          <TextInput ariaLabel="AWS profile" value={profile} onChange={setProfile} placeholder="AWS profile (leave empty to keep current)" size="md" surface="high" />
        </>
      ) : (
        <TextInput ariaLabel="Endpoint" value={endpoint} onChange={setEndpoint} placeholder="Endpoint (leave empty to keep current)" size="md" surface="high" />
      )}
      <TextInput ariaLabel="Default model" value={model} onChange={setModel} placeholder="Default model (optional)" size="md" surface="high" />
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} loading={saving}>Save</Button>
        <Button variant="ghost" size="sm" onClick={() => onDone(false)}>Cancel</Button>
        {error && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{error}</span>}
      </div>
    </div>
  )
}
