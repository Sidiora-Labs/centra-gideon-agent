import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { api, type ImageTier, type ModelRateBody, type ModelRateFields, type ModelRateProvenance, type ModelRateUnit, type ModelRatesView } from '../../shared/data/api'
import { useQuery, writeQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { Field, FieldError, Select, TextInput } from '../../shared/ui/forms'
import { LoadError, ListSkeleton } from '../../shared/ui/ListScaffold'
import { Section } from './settingsUI'

const PER: Record<ModelRateUnit, string> = { token: 'per 1M tokens', image: 'per image', second: 'per second of video', minute: 'per minute of audio', character: 'per 1M characters' }
const UNITS = Object.entries(PER).map(([value, label]) => ({ value, label }))
const KEY = 'settings:model-rates'
export interface RateDraft {
  key: string; unit: ModelRateUnit; input: string; output: string; cacheRead: string; cacheWrite: string
  price: string; byTier: boolean; tiers: Array<{ size: string; quality: string; price: string }>
  defaultSize: string; defaultQuality: string
}
const blankTier = () => ({ size: '', quality: '', price: '' })
const blank = (): RateDraft => ({ key: '', unit: 'token', input: '', output: '', cacheRead: '', cacheWrite: '', price: '', byTier: false, tiers: [blankTier()], defaultSize: '', defaultQuality: '' })
const text = (value: number | null | undefined) => value == null ? '' : String(value)
export function draftOf(key: string, rate?: ModelRateFields | null): RateDraft {
  return { ...blank(), key, unit: rate?.unit || 'token', input: text(rate?.in_per_mtok), output: text(rate?.out_per_mtok), cacheRead: text(rate?.cache_read_per_mtok), cacheWrite: text(rate?.cache_write_per_mtok), price: text(rate?.per_unit), byTier: !!rate?.tiers.length, tiers: rate?.tiers.length ? rate.tiers.map((tier) => ({ size: tier.size, quality: tier.quality, price: String(tier.per_image) })) : [blankTier()], defaultSize: rate?.default_size || '', defaultQuality: rate?.default_quality || '' }
}
function validSize(value: string) {
  return /^\d+x\d+$/i.test(value) && value.toLowerCase().split('x').every((n) => Number(n) > 0)
}
export function draftProblem(draft: RateDraft): string {
  if (!draft.key.trim() || draft.key.length > 200 || /[\x00-\x1f]/.test(draft.key)) return 'Choose a model or enter a model pattern, at most 200 characters.'
  const price = (value: string, label: string, required = true) => !value.trim() ? (required ? `Give the ${label}.` : '') : !Number.isFinite(Number(value)) || Number(value) < 0 ? `The ${label} must be zero or more dollars.` : ''
  if (draft.unit === 'token') return price(draft.input, 'input price') || price(draft.output, 'output price') || price(draft.cacheRead, 'cache read price', false) || price(draft.cacheWrite, 'cache write price', false)
  if (draft.unit === 'image' && draft.byTier) {
    if (!draft.tiers.length || draft.tiers.length > 24) return 'Give between 1 and 24 image prices.'
    for (const tier of draft.tiers) {
      if (tier.size.trim() && !validSize(tier.size.trim())) return 'Use a size such as 1024x1024, with positive dimensions.'
      const problem = price(tier.price, 'price per image'); if (problem) return problem
    }
    if (draft.defaultSize.trim() && !validSize(draft.defaultSize.trim())) return 'Use a positive default size such as 1024x1024.'
    return ''
  }
  return price(draft.price, 'price')
}
export function rateOf(draft: RateDraft): ModelRateBody {
  const common = { key: draft.key.trim(), unit: draft.unit }
  const optional = (value: string) => value.trim() ? Number(value) : null
  if (draft.unit === 'token') return { ...common, in_per_mtok: Number(draft.input), out_per_mtok: Number(draft.output), cache_read_per_mtok: optional(draft.cacheRead), cache_write_per_mtok: optional(draft.cacheWrite) }
  if (draft.unit === 'image' && draft.byTier) return { ...common, tiers: draft.tiers.map((tier) => ({ size: tier.size.trim().toLowerCase(), quality: tier.quality.trim().toLowerCase(), per_image: Number(tier.price) })), default_size: draft.defaultSize.trim().toLowerCase(), default_quality: draft.defaultQuality.trim().toLowerCase() }
  const field = { image: 'per_image', second: 'per_second', minute: 'per_minute', character: 'per_mchar' }[draft.unit]
  return { ...common, [field]: Number(draft.price) }
}
export function fmtRate(usd: number) { return `$${Number(usd.toFixed(6))}` }
export function rateText(rate: ModelRateFields): string {
  if (!rate.unit) return 'unpriced'
  if (rate.unit === 'token') {
    if (rate.in_per_mtok == null || rate.out_per_mtok == null) return 'unpriced'
    const caches = [rate.cache_read_per_mtok == null ? '' : `${fmtRate(rate.cache_read_per_mtok)} cache read`, rate.cache_write_per_mtok == null ? '' : `${fmtRate(rate.cache_write_per_mtok)} cache write`].filter(Boolean)
    return `${fmtRate(rate.in_per_mtok)} input · ${fmtRate(rate.out_per_mtok)} output${caches.length ? ` · ${caches.join(' · ')}` : ''} per 1M tokens`
  }
  if (rate.tiers.length) return `${rate.tiers.map((tier: ImageTier) => `${tier.size ? `up to ${tier.size}` : 'any size'}${tier.quality ? ` ${tier.quality}` : ''}: ${fmtRate(tier.per_image)}`).join(' · ')} per image${rate.default_size || rate.default_quality ? ` · default ${[rate.default_size, rate.default_quality].filter(Boolean).join(' ')}` : ''}`
  return rate.per_unit == null ? 'unpriced' : `${fmtRate(rate.per_unit)} ${PER[rate.unit]}`
}
export function sourceText(rate: ModelRateProvenance): string {
  if (rate.source === 'local') return 'runs on this machine · known $0'
  if (rate.source === 'overlay') return `your price${rate.recorded ? ` · set ${rate.recorded}` : ''}`
  if (rate.source === 'app_default') return `${rate.vendor || 'provider'} app price`
  if (rate.source === 'builtin') return `${rate.vendor || 'maker'} list price${rate.recorded ? ` · recorded ${rate.recorded}` : ''}${rate.priced_as ? ` · ${rate.priced_as}` : ''}`
  return 'no price available'
}
function modelName(ref: string) {
  const sep = ref.indexOf(':'); return sep < 0 ? ref : `${ref.slice(sep + 1)} · ${ref.slice(0, sep)}`
}

export function RateForm({ initial, locked, choices, onSaved, onCancel }: { initial: RateDraft; locked: boolean; choices: string[]; onSaved: (next: ModelRatesView) => void; onCancel: () => void }) {
  const [draft, setDraft] = useState(initial)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const problem = draftProblem(draft)
  const set = (field: keyof RateDraft, value: unknown) => setDraft((current) => ({ ...current, [field]: value }))
  const save = async () => {
    if (problem) return
    setSaving(true); setError('')
    try { onSaved(await api.setModelRate(rateOf(draft))) } catch (failure) { setError(`Price was not saved: ${String((failure as Error)?.message || failure)}`) } finally { setSaving(false) }
  }
  const input = (label: string, field: 'input' | 'output' | 'cacheRead' | 'cacheWrite' | 'price', hint?: string) => <Field label={label} hint={hint}><TextInput type="number" min={0} value={draft[field]} onChange={(value) => set(field, value)} size="md" surface="high" /></Field>
  return <div className="flex flex-col gap-m rounded-lg bg-surface-container px-m py-m">
    {!locked && <>
      <Field label="Model"><Select value={choices.includes(draft.key) ? draft.key : ''} onChange={(value) => set('key', value)} options={[{ value: '', label: 'Other model or pattern' }, ...choices.map((ref) => ({ value: ref, label: modelName(ref) }))]} size="md" surface="high" /></Field>
      {!choices.includes(draft.key) && <Field label="Model or pattern" hint="Use provider:model, a model name, or a pattern such as provider:claude-*."><TextInput value={draft.key} onChange={(value) => set('key', value)} maxLength={200} size="md" surface="high" /></Field>}
    </>}
    <Field label="Billed unit"><Select value={draft.unit} onChange={(value) => set('unit', value)} options={UNITS} size="md" surface="high" /></Field>
    {draft.unit === 'token' && <div className="grid grid-cols-1 gap-m sm:grid-cols-2">{input('Input, $ per 1M tokens', 'input')}{input('Output, $ per 1M tokens', 'output')}{input('Cache read, $ per 1M tokens', 'cacheRead', 'Optional; defaults to the input price.')}{input('Cache write, $ per 1M tokens', 'cacheWrite', 'Optional; no separate write charge when unset.')}</div>}
    {draft.unit === 'image' && <Field label="Image price"><Select value={draft.byTier ? 'tiers' : 'one'} onChange={(value) => set('byTier', value === 'tiers')} options={[{ value: 'one', label: 'One price per image' }, { value: 'tiers', label: 'By size and quality' }]} size="md" surface="high" /></Field>}
    {draft.unit === 'image' && draft.byTier ? <>
      {draft.tiers.map((tier, index) => <div key={index} className="grid grid-cols-1 gap-s sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end">
        {(['size', 'quality', 'price'] as const).map((field) => <Field key={field} label={`${field === 'size' ? 'Size up to' : field === 'quality' ? 'Quality' : '$ per image'}, price ${index + 1}`}><TextInput value={tier[field]} onChange={(value) => set('tiers', draft.tiers.map((current, i) => i === index ? { ...current, [field]: value } : current))} type={field === 'price' ? 'number' : 'text'} min={field === 'price' ? 0 : undefined} size="md" surface="high" /></Field>)}
        <Button variant="ghost" size="sm" ariaLabel={`Remove image price ${index + 1}`} disabled={draft.tiers.length === 1} disabledReason="Keep at least one image price." onClick={() => set('tiers', draft.tiers.filter((_, i) => i !== index))}><X size={14} /></Button>
      </div>)}
      <Button variant="secondary" disabled={draft.tiers.length >= 24} onClick={() => set('tiers', [...draft.tiers, blankTier()])}>Add size or quality</Button>
      <div className="grid grid-cols-1 gap-m sm:grid-cols-2"><Field label="Default size"><TextInput value={draft.defaultSize} onChange={(value) => set('defaultSize', value)} placeholder="1024x1024" size="md" surface="high" /></Field><Field label="Default quality"><TextInput value={draft.defaultQuality} onChange={(value) => set('defaultQuality', value)} placeholder="standard" size="md" surface="high" /></Field></div>
    </> : draft.unit !== 'token' && input(`Price, $ ${PER[draft.unit]}`, 'price')}
    {error && <FieldError>{error}</FieldError>}
    <div className="flex gap-s"><Button size="sm" loading={saving} disabled={!!problem || saving} disabledReason={problem || undefined} onClick={save}>Save price</Button><Button size="sm" variant="secondary" onClick={onCancel}>Cancel</Button></div>
  </div>
}

export function ModelPricesSection() {
  const { data, error, refresh } = useQuery(KEY, () => api.modelRates(), { persist: false })
  const [editing, setEditing] = useState<{ initial: RateDraft; locked: boolean } | null>(null)
  const [failed, setFailed] = useState('')
  const [resetting, setResetting] = useState('')
  const saved = (next: ModelRatesView) => { writeQuery(KEY, next); setEditing(null) }
  const reset = async (key: string) => {
    setResetting(key); setFailed('')
    try { writeQuery(KEY, await api.clearModelRate(key)) } catch (failure) { setFailed(`Price was not reset: ${String((failure as Error)?.message || failure)}`) } finally { setResetting('') }
  }
  if (!data) return <Section title="Model prices">{error ? <LoadError what="model prices" error={error} onRetry={refresh} /> : <ListSkeleton rows={2} what="model prices" />}</Section>
  const refs = new Set(data.models.map((model) => model.ref))
  const overrides = new Map(data.rates.map((row) => [row.key, row]))
  const rows = [...data.models.map((model) => ({ key: model.ref, rate: model, own: overrides.has(model.ref), defaultRate: model.default })), ...data.rates.filter((row) => !refs.has(row.key)).map((row) => ({ key: row.key, rate: row, own: true, defaultRate: null }))]
  return <Section title="Model prices" hint="Prices for bound models and models used in the last 30 days. Your price takes precedence. Maker list prices reflect their recorded date; set your own when your provider bills differently. A model without a price stays unpriced. Setting $0 declares it free.">
    <div className="flex flex-col gap-s">
      {data.unreadable && <FieldError>{data.unreadable}</FieldError>}
      {!rows.length && <p data-type="body-s" className="text-on-surface-low">Bind a model in Settings → Models, or add a price below.</p>}
      {rows.map(({ key, rate, own, defaultRate }) => <div key={key} className="flex flex-col gap-s rounded-lg bg-surface-container px-m py-m">
        <div className="flex flex-col gap-s sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0"><div data-type="body-s" className="break-words text-on-surface">{modelName(key)}</div><p data-type="caption" className="text-on-surface-low">{rateText(rate)} · {sourceText(rate)}</p>{own && <p data-type="caption" className="text-on-surface-low">Reset: {defaultRate ? `${rateText(defaultRate)} · ${sourceText(defaultRate)}` : 'no default price; this model becomes unpriced.'}</p>}</div>
          <div className="flex shrink-0 gap-xs"><Button size="sm" variant="secondary" disabled={!!data.unreadable} onClick={() => setEditing({ initial: draftOf(key, rate.source === 'local' ? null : rate), locked: refs.has(key) })}>{own ? 'Edit price' : 'Set your price'}</Button>{own && <Button size="sm" variant="ghost" loading={resetting === key} disabled={!!data.unreadable || !!resetting} onClick={() => reset(key)}>{defaultRate ? 'Reset to default' : 'Remove price'}</Button>}</div>
        </div>
        {editing?.locked && editing.initial.key === key && <RateForm key={key} {...editing} choices={[...refs]} onSaved={saved} onCancel={() => setEditing(null)} />}
      </div>)}
      {failed && <FieldError>{failed}</FieldError>}
      {editing && !editing.locked ? <RateForm key={editing.initial.key} {...editing} choices={[...refs]} onSaved={saved} onCancel={() => setEditing(null)} /> : <Button variant="secondary" disabled={!!data.unreadable} onClick={() => setEditing({ initial: blank(), locked: false })}><Plus size={14} /> Add a price</Button>}
    </div>
  </Section>
}
