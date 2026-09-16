import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
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
  if (def.layer <= LAYER_CORE) return node
  return <LayerBoundary layer={def.layer} what={def.name}>{node}</LayerBoundary>
}

export function GenUiNodes({ content }: { content: string }) {
  const program = useMemo(() => planGenUiProgram(parseGenUi(content || '')), [content])
  return <>
    {program.roots.map(line => <div key={line.id} data-genui-node={line.id}>{renderInvocation(line, program.byId, [])}</div>)}
    {program.parseErrors.map(error => <DroppedLine key={`parse:${error.line}`} message={`Line ${error.line}: ${error.message} — "${error.text.slice(0, 60)}"`} />)}
  </>
}

export const GenUiWidget = memo(function GenUiWidget({ content, title, slug }: EmbedProps) {
  const host = useGenUiHost()
  const [actionError, setActionError] = useState('')
  const lifecycle = useRef({ active: true, action: 0 })
  useEffect(() => {
    lifecycle.current.active = true
    return () => { lifecycle.current.active = false; lifecycle.current.action++ }
  }, [])
  const emit = useCallback<GenUiEmit>(async input => {
    const attempt = ++lifecycle.current.action
    setActionError('')
    const dual = composeDualPayload({ ...input, live: slug ? { saved: true, slug } : undefined })
    const result = dual
      ? await routeGenUiAction(dual, host.producer, { action: input.action, payload: input.payload })
      : { ok: false, message: 'That action could not be sent — its values are not serializable.' }
    if (!lifecycle.current.active) return
    if (result.ok) host.onResolved?.()
    else if (attempt === lifecycle.current.action) setActionError(result.message || 'That action could not be completed.')
  }, [host, slug])

  return <Surface tone="low" radius="lg" className="my-3 overflow-hidden border border-outline-variant/35">
    <header className="flex items-center gap-2 border-b border-outline-variant/30 bg-surface-container px-4 py-2">
      <PanelsTopLeft aria-hidden size={15} className="shrink-0 text-primary" />
      <span data-type="label-s" className="min-w-0 truncate font-medium text-on-surface">{title || 'Widget'}</span>
    </header>
    <GenUiActionCtx.Provider value={emit}>
      <div className="flex flex-col gap-3 p-4" data-gideon-genui="true">
        <GenUiNodes content={content || ''} />
        {actionError && <DroppedLine message={actionError} />}
      </div>
    </GenUiActionCtx.Provider>
  </Surface>
})
