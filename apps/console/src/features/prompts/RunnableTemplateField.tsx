import { Rocket } from 'lucide-react'
import type { LaunchSpec } from '../../shared/data/api'
import { accentChip } from '../../shared/theme/accent'

const selections = [
  { key: 'kind', label: 'Kind', fallback: 'goal', options: [['goal', 'Goal'], ['general', 'General'], ['code', 'Code']] },
  { key: 'intake_rigor', label: 'Intake depth', fallback: 'minimal', options: [['minimal', 'minimal'], ['grill', 'grill'], ['thorough', 'thorough']] },
] as const
const textFields = [{ key: 'agent', label: 'Agent (optional)', placeholder: 'default worker' }, { key: 'model', label: 'Model (optional)', placeholder: 'active model' }] as const
const controlClass = 'h-8 rounded-md border border-outline-variant/30 bg-surface px-s text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
export function RunnableTemplateField({ spec, onChange }: { spec?: LaunchSpec; onChange: (spec: LaunchSpec | undefined) => void }) {
  const enabled = spec != null
  const update = (key: keyof LaunchSpec, value: string) => onChange({ ...spec, [key]: value })
  return <div className="grid gap-m">
    <button type="button" aria-pressed={enabled} onClick={() => onChange(enabled ? undefined : { kind: 'goal', intake_rigor: 'minimal' })} data-type="body-s" className="inline-flex min-h-8 items-center justify-self-start gap-s rounded-md border border-outline-variant/25 px-m" style={enabled ? accentChip : { color: 'var(--color-on-surface-var)', background: 'var(--color-surface-container)' }}><Rocket size={14} />{enabled ? 'Runnable — launches a loop' : 'Make runnable'}</button>
    {enabled && <div className="grid gap-m rounded-lg border border-outline-variant/30 bg-surface-container/30 p-m"><div className="grid grid-cols-2 gap-m">
      {selections.map(field => <label key={field.key} data-type="caption" className="grid gap-1 text-on-surface-var">{field.label}<select value={spec[field.key] ?? field.fallback} onChange={event => update(field.key, event.target.value)} data-type="body-s" className={controlClass}>{field.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}
      {textFields.map(field => <label key={field.key} data-type="caption" className="grid gap-1 text-on-surface-var">{field.label}<input value={spec[field.key] ?? ''} onChange={event => update(field.key, event.target.value)} placeholder={field.placeholder} data-type="body-s" className={`${controlClass} font-mono placeholder:text-on-surface-low`} /></label>)}
    </div><p data-type="caption" className="text-on-surface-low">The variables above are filled at launch, rendered into the task, then this loop is created + started.</p></div>}
  </div>
}
