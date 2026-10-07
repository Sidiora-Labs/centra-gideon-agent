import { TextInput } from '../../shared/ui/forms'
import { useId, useState } from 'react'
import { X } from 'lucide-react'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import type { PromptVariable, PromptVarType } from '../../shared/data/api'
import { VAR_TYPES } from './promptMeta'

const inputStyle = 'min-w-0 h-8 rounded-md border border-outline-variant/25 bg-surface px-m text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
export function VariableRow({ v, onChange, onRemove, descriptionPlaceholder = 'Description (shown when invoked)', rowIndex }: {
  v: PromptVariable; onChange: (patch: Partial<PromptVariable>) => void; onRemove: () => void; descriptionPlaceholder?: string; rowIndex?: number
}) {
  const rid = useId()
  const which = v.name?.trim() ? `"${v.name.trim()}"` : ['row', rowIndex == null ? '' : rowIndex + 1].filter(value => value !== '').join(' ')
  const editName = (text: string) => onChange({ name: Array.from(text, character => /[a-zA-Z0-9_]/.test(character) ? character : '_').join('') })
  return <div className="grid gap-s rounded-lg border border-outline-variant/30 bg-surface-container/30 p-m">
    <div className="flex flex-wrap items-center gap-s">
      <TextInput value={v.name} onChange={nextValue => editName(nextValue)} placeholder="variable_name" ariaLabel={`Name of variable ${which}`} name={`var-name-${rid}`}  className={`${inputStyle} flex-1 font-mono`} size="sm" surface="container" />
      <select value={v.type} onChange={event => onChange({ type: event.target.value as PromptVarType })} aria-label={`Type of variable ${which}`} name={`var-type-${rid}`} data-type="body-s" className={inputStyle}>{VAR_TYPES.map(type => <option key={type.key} value={type.key}>{type.label}</option>)}</select>
      <button type="button" aria-pressed={Boolean(v.required)} onClick={() => onChange({ required: !v.required })} data-type="caption" className="min-h-7 rounded-md border border-outline-variant/30 px-s" style={{ color: v.required ? 'var(--color-danger)' : 'var(--color-on-surface-low)' }}>{v.required ? 'required' : 'optional'}</button>
      <SquareIconButton icon={X} tone="danger" label={`Remove variable ${which}`} onClick={onRemove} />
    </div>
    <div className="flex flex-wrap gap-s">
      <TextInput value={v.description ?? ''} onChange={nextValue => onChange({ description: nextValue })} placeholder={descriptionPlaceholder} ariaLabel={`Description of variable ${which}`} name={`var-desc-${rid}`}  className={`${inputStyle} flex-1`} size="sm" surface="container" />
      <TextInput value={v.default == null ? '' : String(v.default)} onChange={nextValue => onChange({ default: nextValue })} placeholder="default" ariaLabel={`Default value of variable ${which}`} name={`var-default-${rid}`}  className={`${inputStyle} !w-28`} size="sm" surface="container" />
    </div>
    {v.type === 'select' && <ChoicesInput options={v.options} onChange={onChange} name={`var-opts-${rid}`} which={which} />}
  </div>
}
function ChoicesInput({ options, onChange, name, which }: { options?: string[]; onChange: (patch: Partial<PromptVariable>) => void; name: string; which: string }) {
  const committed = (options ?? []).join(', ')
  const [editing, setEditing] = useState<{ baseline: string; text: string } | null>(null)
  const draft = editing?.baseline === committed ? editing.text : committed
  const commit = () => {
    const entries: string[] = []
    for (const part of draft.split(',')) { const value = part.trim(); if (value) entries.push(value) }
    onChange({ options: entries }); setEditing(null)
  }
  return <TextInput value={draft} onChange={nextValue => setEditing({ baseline: committed, text: nextValue })} onBlur={commit} placeholder="Choices, comma-separated" ariaLabel={`Choices for variable ${which}`} name={name}  className={inputStyle} size="sm" surface="container" />
}
