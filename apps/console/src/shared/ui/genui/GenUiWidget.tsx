import { memo, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { AlertTriangle, PanelsTopLeft } from 'lucide-react'
import { Surface } from '../Surface'
import type { EmbedProps } from '../content/contentTypes'
import { LAYER_CORE, maxSurfaceLayer } from '../surfaces/layers'
import { LayerBoundary } from '../surfaces/LayerBoundary'
import { parseGenUi, type ParsedLine } from './parse'
import { getComponent, validateInvocation } from './registry'
import { registerCoreGenUiComponents } from './components'
import { GenUiActionCtx, composeDualPayload, routeGenUiAction, useGenUiHost, type GenUiEmit } from './actions'
import { planGenUiProgram } from './programGraph'
import { parseGenUiEnvelope, type GenUiV2Envelope } from './envelope'
import {
  GenUiElementCtx,
  GenUiWidgetStateProvider,
  getGenUiPersistenceKey,
  useGenUiWidgetEnvelope,
} from './widgetState'

registerCoreGenUiComponents()

function DroppedLine({ message }: { message: string }) {
  return <div role="alert" data-type="caption" className="flex items-start gap-2 rounded-md border-l-2 border-danger/60 bg-danger/5 px-3 py-2 text-danger">
    <AlertTriangle aria-hidden size={14} className="mt-0.5 shrink-0" /><span className="min-w-0 break-words">{message}</span>
  </div>
}

function renderInvocation(line: ParsedLine, byId: Map<string, ParsedLine>, ancestors: readonly string[]): ReactNode {
  if (ancestors.includes(line.id)) return <DroppedLine message={`Reference cycle: ${[...ancestors, line.id].join(' → ')}.`} />
  const invalid = validateInvocation(line.component, line.argKeys)
  if (invalid) return <DroppedLine message={invalid.message} />
  const def = getComponent(line.component)!
  if (def.layer > maxSurfaceLayer()) return <DroppedLine message={`Component "${line.component}" is unavailable in safe mode.`} />
  const path = [...ancestors, line.id]
  const children: Record<string, ReactNode> = Object.create(null)
  for (const [argument, references] of Object.entries(line.refs)) {
    children[argument] = references.map((id, index) => {
      const child = byId.get(id)
      return child ? <div key={`${id}:${index}`} data-genui-child={id}>{renderInvocation(child, byId, path)}</div> : null
    })
  }
  const Component = def.component
  const node = <Component args={line.args} children={children} />
  const layered = def.layer <= LAYER_CORE ? node : <LayerBoundary layer={def.layer} what={def.name}>{node}</LayerBoundary>
  return <GenUiElementCtx.Provider value={line.id}>{layered}</GenUiElementCtx.Provider>
}

export function GenUiNodes({ content }: { content: string }) {
  const program = useMemo(() => planGenUiProgram(parseGenUi(content || '')), [content])
  return <>
    {program.roots.map(line => <div key={line.id} data-genui-node={line.id}>{renderInvocation(line, program.byId, [])}</div>)}
    {program.parseErrors.map(error => <DroppedLine key={`parse:${error.line}`} message={`Line ${error.line}: ${error.message} — "${error.text.slice(0, 60)}"`} />)}
  </>
}

function renderV2Invocation(envelope: GenUiV2Envelope, id: string): ReactNode {
  const element = envelope.elements[id]
  const def = getComponent(element.type)
  if (!def) return <DroppedLine message={`Component "${element.type}" has no installed renderer.`} />
  if (def.layer > maxSurfaceLayer()) return <DroppedLine message={`Component "${element.type}" is unavailable in safe mode.`} />
  const children: Record<string, ReactNode> = Object.create(null)
  if (element.children) {
    children.body = element.children.map(child =>
      <div key={child} data-genui-child={child}>{renderV2Invocation(envelope, child)}</div>)
  }
  const Component = def.component
  const component = <Component args={element.props} children={children} />
  const node = def.layer <= LAYER_CORE ? component : <LayerBoundary layer={def.layer} what={def.name}>{component}</LayerBoundary>
  return <GenUiElementCtx.Provider value={id}>{node}</GenUiElementCtx.Provider>
}

function GenUiV2Tree() {
  const { envelope, error } = useGenUiWidgetEnvelope()
  return <>
    {envelope && <div data-genui-node={envelope.root}>{renderV2Invocation(envelope, envelope.root)}</div>}
    {error && <DroppedLine message={error} />}
  </>
}

function GenUiContent({ content, streaming, host }: { content: string; streaming?: boolean; host: ReturnType<typeof useGenUiHost> }) {
  const parsed = useMemo(() => parseGenUiEnvelope(content || ''), [content])
  if (parsed.kind === 'legacy') return <GenUiNodes content={content || ''} />
  if (parsed.kind === 'invalid') return <DroppedLine message={`Invalid GenUI v2: ${parsed.message}`} />
  if (parsed.kind === 'incomplete') {
    return streaming
      ? <div role="status" data-type="caption" className="text-on-surface-low">Loading interface…</div>
      : <DroppedLine message={`Invalid GenUI v2: ${parsed.message}`} />
  }
  const key = getGenUiPersistenceKey(host, parsed.envelope.id) ?? `ephemeral:${parsed.envelope.id}`
  return <GenUiWidgetStateProvider key={key} envelope={parsed.envelope} host={host}><GenUiV2Tree /></GenUiWidgetStateProvider>
}

export const GenUiWidget = memo(function GenUiWidget({ content, title, slug, streaming }: EmbedProps) {
  const host = useGenUiHost()
  const lifecycle = useRef({ active: true, action: 0 })
  useEffect(() => {
    lifecycle.current.active = true
    return () => { lifecycle.current.active = false; lifecycle.current.action++ }
  }, [])
  const emit = useCallback<GenUiEmit>(async input => {
    ++lifecycle.current.action
    const dual = composeDualPayload({ ...input, live: slug ? { saved: true, slug } : undefined })
    const result = dual
      ? await routeGenUiAction(dual, host.producer, { action: input.action, payload: input.payload })
      : { ok: false as const, outcome: 'error' as const, message: 'That action could not be sent — its values are not serializable.' }
    if (!lifecycle.current.active) return result
    if (result.ok) host.onResolved?.()
    return result
  }, [host, slug])

  return <Surface tone="low" radius="lg" className="my-3 overflow-hidden border border-outline-variant/35">
    <header className="flex items-center gap-2 border-b border-outline-variant/30 bg-surface-container px-4 py-2">
      <PanelsTopLeft aria-hidden size={15} className="shrink-0 text-primary" />
      <span data-type="label-s" className="min-w-0 truncate font-medium text-on-surface">{title || 'Widget'}</span>
    </header>
    <GenUiActionCtx.Provider value={emit}>
      <div className="flex flex-col gap-3 p-4" data-gideon-genui="true">
        <GenUiContent content={content || ''} streaming={streaming} host={host} />
      </div>
    </GenUiActionCtx.Provider>
  </Surface>
})
