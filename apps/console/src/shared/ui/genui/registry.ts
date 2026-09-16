import type { ComponentType, ReactNode } from 'react'
import { LAYER_CORE, layerName, maxSurfaceLayer, type SurfaceLayer } from '../surfaces/layers'

export type GenUiArgType = 'string' | 'number' | 'boolean' | 'string[]' | 'number[]' | 'rows' | 'ref' | 'refs' | 'any'
export interface GenUiArg { key: string; type: GenUiArgType; required?: boolean; note?: string }
export type GenUiGroup = 'Layout' | 'Data' | 'Charts' | 'Feedback' | 'Forms'
export interface GenUiRenderProps { args: Record<string, unknown>; children: Record<string, ReactNode> }
export interface GenUiComponentDef { name: string; group: GenUiGroup; description: string; args: GenUiArg[]; component: ComponentType<GenUiRenderProps> }
export interface GenUiRegistration extends GenUiComponentDef { layer: SurfaceLayer; source: string }
export type GenUiRegisterRefusal = { ok: false; code: 'shadows-core' | 'shadows-layer' | 'layer-disabled' | 'invalid'; message: string }
export type GenUiRegisterResult = { ok: true } | GenUiRegisterRefusal
export type GenUiErrorKind = 'unknown-component' | 'missing-required' | 'excess-args'
export interface GenUiValidationError { kind: GenUiErrorKind; component: string; keys: string[]; message: string }

interface RegistryEntry { definition: GenUiRegistration; declared: Set<string>; required: string[] }
const catalog = new Map<string, RegistryEntry>()
const groups: GenUiGroup[] = ['Layout', 'Data', 'Charts', 'Forms', 'Feedback']

function store(definition: GenUiRegistration) {
  const args = definition.args.map(arg => ({ ...arg }))
  catalog.set(definition.name, {
    definition: { ...definition, args },
    declared: new Set(args.map(arg => arg.key)), required: args.filter(arg => arg.required).map(arg => arg.key),
  })
}

export function defineComponent(def: GenUiComponentDef): void {
  store({ ...def, layer: LAYER_CORE, source: '' })
}

export function registerLayerComponent(def: GenUiComponentDef, opts: { layer: SurfaceLayer; source: string }): GenUiRegisterResult {
  const name = (def.name || '').trim()
  const current = catalog.get(name)?.definition
  const refusals: Array<[boolean, GenUiRegisterRefusal['code'], string]> = [
    [!name || !def.component, 'invalid', 'A genui component needs a name and a renderer.'],
    [opts.layer <= LAYER_CORE, 'invalid', 'Core (L0) components are registered by the shipped bundle, not by a layer.'],
    [opts.layer > maxSurfaceLayer(), 'layer-disabled', `Safe mode is on — ${layerName(opts.layer)}-layer component "${name}" was not registered.`],
    [!!current && current.layer <= LAYER_CORE, 'shadows-core', `"${name}" is a core component — an ${layerName(opts.layer)} layer may add components, never shadow core ones.`],
    [!!current && current.layer <= opts.layer && current.source !== opts.source, 'shadows-layer', `"${name}" is already registered by ${current?.source || (current ? layerName(current.layer) : '')}.`],
  ]
  const refusal = refusals.find(([applies]) => applies)
  if (refusal) return { ok: false, code: refusal[1], message: refusal[2] }
  store({ ...def, name, ...opts })
  return { ok: true }
}

export function removeComponentsFrom(source: string): number {
  if (!source) return 0
  const names = [...catalog].filter(([, entry]) => entry.definition.source === source).map(([name]) => name)
  for (const name of names) catalog.delete(name)
  return names.length
}

export const getComponent = (name: string): GenUiRegistration | undefined => catalog.get(name)?.definition
export const componentLayer = (name: string): SurfaceLayer | null => getComponent(name)?.layer ?? null
export const allComponents = (): GenUiRegistration[] => [...catalog.values()].map(entry => entry.definition)

export function validateInvocation(name: string, argKeys: string[]): GenUiValidationError | null {
  const entry = catalog.get(name)
  const error = (kind: GenUiErrorKind, keys: string[], message: string): GenUiValidationError => ({ kind, keys, component: name, message })
  if (!entry) return error('unknown-component', [], `Unknown component "${name}". Available: ${[...catalog.keys()].join(', ')}.`)
  const provided = new Set(argKeys)
  const missing = entry.required.filter(key => !provided.has(key))
  if (missing.length) return error('missing-required', missing, `${name} is missing required arg${missing.length === 1 ? '' : 's'}: ${missing.join(', ')}.`)
  const excess = argKeys.filter(key => !entry.declared.has(key))
  return excess.length ? error('excess-args', excess, `${name} got unknown arg${excess.length === 1 ? '' : 's'}: ${excess.join(', ')}. Allowed: ${[...entry.declared].join(', ')}.`) : null
}

function componentSignature(definition: GenUiRegistration): string {
  const argumentsText = definition.args.map(arg => [arg.key + (arg.required ? '' : '?'), arg.type].join(': ')).join(', ')
  const description = definition.description ? ` — ${definition.description}` : ''
  const attribution = definition.layer > LAYER_CORE && definition.source ? ` [from the ${definition.source} app]` : ''
  return `  ${definition.name}(${argumentsText})${description}${attribution}`
}

export const library = {
  prompt(): string {
    const sections = groups.flatMap(group => {
      const members = allComponents().filter(definition => definition.group === group)
      return members.length ? [`${group}:`, ...members.map(componentSignature), ''] : []
    })
    return [
      'Generative-UI components you may emit inside a <widget kind="genui"> block.',
      'DSL: one line per component — `id = Component(key: value, …)`. Forward references are legal.',
      'Compose children with a `refs`/`ref` arg holding other line ids (e.g. children: [a, b]).', '',
      ...sections,
      'Actions: a Forms component sends its `label`/`submit` text as the visible message and',
      'its collected values as the machine payload — so write a label a human reads, not a code.', '', 'Example:',
      '  root = Stack(gap: "m", body: [stat, note, ask])',
      '  stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)',
      '  note = Callout(tone: "info", text: "Up 12% vs last quarter.")',
      '  ask = Form(title: "Log an expense", fields: ["amount", "vendor"], action: "log_expense", submit: "Log expense")',
    ].join('\n')
  },
}
