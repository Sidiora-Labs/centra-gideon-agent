import { useEffect, useMemo } from 'react'
import { api, type WorkflowDef, type WorkflowInputParam } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Combobox } from '../../shared/ui/Combobox'
import { InlineError } from '../../shared/ui/InlineError'
import { TextArea } from '../../shared/ui/forms'
import { schemaProps, SchemaField, type JsonSchema, type WidgetMap } from '../tools/schema'

export function workflowInputsSchema(inputs: Record<string, WorkflowInputParam> | undefined): JsonSchema {
  return {
    type: 'object',
    properties: Object.fromEntries(Object.entries(inputs ?? {}).map(([name, input]) => [name, {
      type: input.type || 'string',
      ...(input.default !== undefined ? { default: input.default } : {}),
      'x-meta': { help: input.help || input.description },
    }])),
    required: Object.entries(inputs ?? {}).filter(([, input]) => input.required).map(([name]) => name),
  }
}

export function useWorkflowWidgets(needed: boolean, workflow: string): { widgets: WidgetMap } {
  const widgets = useMemo<WidgetMap>(() => needed ? {
    workflow: ({ value, onChange }) => <WorkflowPicker value={String(value ?? '')} onChange={onChange} />,
    'workflow-inputs': ({ value, onChange }) => <WorkflowInputs workflow={workflow} value={value} onChange={onChange} />,
  } : {} as WidgetMap, [needed, workflow])
  return { widgets }
}

function WorkflowPicker({ value, onChange }: { value: string; onChange: (value: unknown) => void }) {
  const query = useQuery('workflows:action:definitions', () => api.workflowDefs().then(result => result.defs))
  if (query.error) return <InlineError icon onRetry={query.refresh}>Couldn’t load workflows: {String((query.error as Error)?.message || query.error)}</InlineError>
  return <Combobox value={value} onChange={onChange}
    options={(query.data ?? []).map(def => ({ value: def.name, label: def.name, description: def.description }))}
    placeholder={query.loading ? 'Loading workflows…' : 'Pick a workflow…'} emptyText="No workflows" />
}

function WorkflowInputs({ workflow, value, onChange }: { workflow: string; value: unknown; onChange: (value: unknown) => void }) {
  if (!workflow) return <p data-type="body-s" className="text-on-surface-low">Pick a workflow to set its inputs.</p>
  return <DeclaredWorkflowInputs key={workflow} workflow={workflow} value={value} onChange={onChange} />
}

function DeclaredWorkflowInputs({ workflow, value, onChange }: { workflow: string; value: unknown; onChange: (value: unknown) => void }) {
  const query = useQuery(`workflows:action:definition:${workflow}`, () => api.workflowDef(workflow).then(result => result.definition))
  if (query.error) return <InlineError icon onRetry={query.refresh}>Couldn’t load {workflow} inputs: {String((query.error as Error)?.message || query.error)}</InlineError>
  if (!query.data) return <p data-type="body-s" className="text-on-surface-low">Loading inputs…</p>
  return <WorkflowInputFields definition={query.data} value={value} onChange={onChange} />
}

export function WorkflowInputFields({ definition, value, onChange }: { definition: WorkflowDef; value: unknown; onChange: (value: unknown) => void }) {
  const schema = useMemo(() => workflowInputsSchema(definition.inputs), [definition.inputs])
  const { props, required } = useMemo(() => schemaProps(schema), [schema])
  const parsed = useMemo(() => {
    if (value === '' || value == null) return {}
    try {
      const candidate = typeof value === 'string' ? JSON.parse(value) : value
      return candidate && typeof candidate === 'object' && !Array.isArray(candidate) ? candidate as Record<string, unknown> : null
    } catch { return null }
  }, [value])
  useEffect(() => {
    if (!parsed) return
    const defaults = Object.fromEntries(props.filter(([name, input]) => parsed[name] === undefined && input.default !== undefined).map(([name, input]) => [name, input.default]))
    if (Object.keys(defaults).length) onChange({ ...parsed, ...defaults })
  }, [parsed, props, onChange])
  if (!parsed) return <div className="flex flex-col gap-m"><InlineError icon>Inputs must be a JSON object. Correct the saved value before continuing.</InlineError>
    <TextArea value={typeof value === 'string' ? value : JSON.stringify(value)} onChange={onChange} ariaLabel="Workflow inputs JSON" rows={4} /></div>
  if (!props.length) return <p data-type="body-s" className="text-on-surface-low">{definition.name} takes no inputs.</p>
  return <div className="rounded-md border border-outline-variant/40 bg-surface-container/40 px-m py-m flex flex-col gap-m">
    {props.map(([name, input]) => <SchemaField key={name} name={name} schema={input} required={required.has(name)} value={parsed[name]} onChange={next => {
      let typed = next
      if ((input.type === 'object' || input.type === 'array') && typeof next === 'string') {
        try { typed = JSON.parse(next) } catch {}
      }
      onChange({ ...parsed, [name]: typed })
    }} />)}
  </div>
}
