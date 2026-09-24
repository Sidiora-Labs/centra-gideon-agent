import { useState } from 'react'
import { api, type PromptBinding, type PromptItem, type PromptBindings } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section } from './settingsUI'
import { LoadError, ListSkeleton } from '../../shared/ui/ListScaffold'

export function PromptsPanel() {
  const { data, error: loadErr, refresh } = useQuery<PromptBindings | null>(
    'settings:prompt-bindings', () => api.promptBindings(), { persist: true },
  )
  const [saving, setSaving] = useState('')

  const onPick = async (useCase: string, ref: string) => {
    setSaving(useCase)
    try {
      await api.setPromptBinding(useCase, ref)
      refresh()
    } finally {
      setSaving('')
    }
  }

  return (
    <div>
      <PanelHeader title="Prompts" hint="Bind which prompt serves each runtime context. Edit the prompts themselves on the Prompts page; unset uses each context's bundled default." />
      {loadErr ? <LoadError what="prompt bindings" error={loadErr} onRetry={refresh} /> : !data ? (
        <ListSkeleton rows={4} />
      ) : (
        data.categories.map((c) => {
          const rows = data.bindings.filter((b) => b.category === c.key)
          if (rows.length === 0) return null
          return (
            <Section key={c.key} title={c.label} hint={c.hint}>
              <div className="flex flex-col gap-2">
                {rows.map((b) => (
                  <BindingRow key={b.use_case} binding={b} available={data.available}
                    saving={saving === b.use_case} onPick={onPick} />
                ))}
              </div>
            </Section>
          )
        })
      )}
    </div>
  )
}

function BindingRow({ binding, available, saving, onPick }: {
  binding: PromptBinding
  available: PromptItem[]
  saving: boolean
  onPick: (useCase: string, ref: string) => void
}) {
  const defName = binding.effective_ref.split(':').slice(1).join(':')
  return (
    <div className="flex items-center gap-3 rounded-lg bg-surface-container px-4 py-3">
      <div className="min-w-0 flex-1">
        <div data-type="body-s" className="text-on-surface">{binding.label}</div>
        {binding.hint && <div data-type="body-s" className="mt-0.5 text-on-surface-low">{binding.hint}</div>}
      </div>
      <select
        value={binding.ref}
        disabled={saving}
        onChange={(e) => onPick(binding.use_case, e.target.value)}
        aria-label={`Prompt for ${binding.label}`}
        data-type="body-s" className="shrink-0 max-w-[55%] rounded-md bg-surface-high px-2 py-1.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
      >
        <option value="">Default ({defName})</option>
        {
}
        {available.filter((p) => (p.kind ?? 'system') === 'system').map((p) => (
          <option key={p.name} value={`native:${p.name}`}>{p.title || p.name}</option>
        ))}
      </select>
    </div>
  )
}
