import { useEffect, useMemo } from 'react'
import { type ActionProvider, type PromptItem, type PromptVariable } from '../../shared/data/api'
import { Combobox } from '../../shared/ui/Combobox'
import { Field, TextArea } from '../../shared/ui/forms'
import { InlineError } from '../../shared/ui/InlineError'
import { buildArgs, schemaProps, SchemaField, SchemaFieldDisclosure } from '../tools/schema'
import { usePromptWidgets } from '../tools/usePromptWidgets'
import { actionIcon } from './triggerMeta'

export function ActionConfig({ providers, provider, config, onProvider, onConfig, vars, loadError, onRetryProviders }: {
  providers: ActionProvider[]
  provider: string
  config: Record<string, unknown>
  onProvider: (name: string) => void
  onConfig: (cfg: Record<string, unknown>) => void
  vars: string[]
  loadError?: unknown
  onRetryProviders?: () => void
}) {
  const selected = providers.find((p) => p.name === provider)
  const options = useMemo(() => providers.map((p) => ({
    value: p.name, label: p.display_name, description: p.supports_blocking ? 'can block the event' : undefined,
  })), [providers])
  const { props, required } = useMemo(() => schemaProps(selected?.settingsSchema), [selected])

  const setField = (k: string, v: unknown) => onConfig({ ...config, [k]: v })

  const { prompts, widgets } = usePromptWidgets(props.map(([, schema]) => schema))

  return (
    <div className="flex flex-col gap-l">
      <Field label="Action" hint="What runs when this trigger fires. Provided by a registered action provider.">
        {loadError ? (
          <InlineError icon onRetry={onRetryProviders}>
            Couldn&rsquo;t load the action providers{(loadError as Error)?.message ? `: ${(loadError as Error).message}` : '.'}
          </InlineError>
        ) : (
          <Combobox options={options} value={provider} onChange={onProvider} placeholder="Pick an action…" emptyText="No action providers" />
        )}
      </Field>

      {selected && (
        <>
          {vars.length > 0 && (
            <div className="rounded-md bg-surface-container/60 px-m py-2">
              <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">Available variables</div>
              <div className="flex flex-wrap gap-1.5">
                {vars.map((v) => (
                  <span key={v} className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center font-mono text-on-surface-var text-[0.75rem]" title="Use this in any template field below">{v}</span>
                ))}
              </div>
            </div>
          )}

          {props.length === 0 ? (
            <p className="text-on-surface-low text-[0.8125rem]">This action takes no configuration.</p>
          ) : (
            <div className="flex flex-col gap-m">
              <SchemaFieldDisclosure fields={props} required={required} values={config}
                renderField={([name, schema]) => <SchemaField key={name} name={name} schema={schema} required={required.has(name)}
                  value={config[name]} onChange={(v) => setField(name, v)} widgets={widgets} />} />
            </div>
          )}

          {
}
          <PromptVarsFields prompts={prompts} promptId={String(config.prompt_id ?? '')}
            vars={(config.vars as Record<string, unknown>) || {}}
            onVars={(v) => setField('vars', v)} />
        </>
      )}
    </div>
  )
}

function PromptVarsFields({ prompts, promptId, vars, onVars }: {
  prompts: PromptItem[]
  promptId: string
  vars: Record<string, unknown>
  onVars: (v: Record<string, unknown>) => void
}) {
  const selected = prompts.find((p) => p.name === promptId)
  const declared: PromptVariable[] = selected?.merged_variables || selected?.variables || []

  useEffect(() => {
    const missing: Record<string, unknown> = {}
    for (const v of declared) {
      if (v.default != null && vars[v.name] === undefined) missing[v.name] = v.default
    }
    if (Object.keys(missing).length > 0) onVars({ ...vars, ...missing })
  }, [promptId, declared.length])  // eslint-disable-line react-hooks/exhaustive-deps

  if (!promptId || declared.length === 0) return null

  const setVar = (name: string, value: unknown) => onVars({ ...vars, [name]: value })
  const valOf = (v: PromptVariable) => {
    const cur = vars[v.name]
    return cur !== undefined && cur !== null ? String(cur) : (v.default != null ? String(v.default) : '')
  }

  return (
    <div className="rounded-md border border-outline-variant/40 bg-surface-container/40 px-m py-3 flex flex-col gap-m">
      <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">
        {selected?.name} variables
      </div>
      {declared.map((v) => (
        <Field key={v.name} label={v.name + (v.required ? ' *' : '')} hint={v.description}>
          {v.type === 'select' && v.options && v.options.length > 0 ? (
            <select
              value={valOf(v)} onChange={(e) => setVar(v.name, e.target.value)}
              className="w-full rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
              {!v.required && <option value="">—</option>}
              {v.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : v.type === 'textarea' ? (
            <TextArea value={valOf(v)} onChange={(val) => setVar(v.name, val)} rows={3} ariaLabel={v.name} />
          ) : (
            <input
              value={valOf(v)} onChange={(e) => setVar(v.name, e.target.value)} aria-label={v.name}
              className="w-full rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          )}
        </Field>
      ))}
    </div>
  )
}

export function seedActionConfig(provider: ActionProvider | undefined): Record<string, unknown> {
  if (!provider) return {}
  const { props } = schemaProps(provider.settingsSchema)
  const out: Record<string, unknown> = {}
  for (const [k, s] of props) {
    if (s.default !== undefined) out[k] = s.default
    else if (s.type === 'boolean') out[k] = false
    else out[k] = ''
  }
  return out
}

export function coerceActionConfig(
  providers: ActionProvider[],
  provider: string,
  config: Record<string, unknown>,
): { config: Record<string, unknown>; error?: string } {
  const selected = providers.find((p) => p.name === provider)
  if (!selected) return { config }
  const { args, error } = buildArgs(selected.settingsSchema, config)
  return error ? { config, error } : { config: args }
}

export { actionIcon }
