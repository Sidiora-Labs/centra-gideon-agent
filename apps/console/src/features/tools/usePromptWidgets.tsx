import { useEffect, useMemo, useState } from 'react'
import { api, type PromptItem } from '../../shared/data/api'
import { Combobox } from '../../shared/ui/Combobox'
import type { SchemaProp } from './schema'
import type { WidgetMap } from './schema'

export function usePromptWidgets(fields: readonly SchemaProp[]): { prompts: PromptItem[]; widgets: WidgetMap } {
  const [prompts, setPrompts] = useState<PromptItem[]>([])
  const needsPrompt = fields.some((field) => field['x-meta']?.widget === 'prompt')

  useEffect(() => {
    if (needsPrompt && prompts.length === 0) api.prompts('user').then(setPrompts).catch(() => {})
  }, [needsPrompt, prompts.length])

  const widgets = useMemo<WidgetMap>(() => ({
    prompt: ({ value, onChange, placeholder }) => (
      <Combobox
        options={prompts.map((prompt) => ({ value: prompt.name, label: prompt.name, description: prompt.description || undefined }))}
        value={String(value ?? '')} onChange={onChange} placeholder={placeholder || 'Pick a saved prompt…'}
        emptyText="No saved prompts" />
    ),
  }), [prompts])

  return { prompts, widgets }
}
