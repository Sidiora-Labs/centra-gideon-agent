import { api, type SurfaceOverlayDoc, type SurfaceOverlayPayload, type SurfaceOverlayRefusal } from '../../data/api'
import { GenUiNodes } from '../genui/GenUiWidget'
import { parseGenUi } from '../genui/parse'
import { registerLayerComponent, removeComponentsFrom, validateInvocation } from '../genui/registry'
import { LAYER_USER, maxSurfaceLayer } from './layers'

export const CODE_OVERLAY_COMPONENT = 'ERR_SURFACE_OVERLAY_COMPONENT'
export const REFUSAL_HOME = 'dashboard'
export interface OverlayRefusalRow extends SurfaceOverlayRefusal { surface: string }
interface OverlaySnapshot { overlays: SurfaceOverlayDoc[]; refusals: OverlayRefusalRow[] }
const sourceOf = (file: string) => `overlay:${file}`

export function overlayComponentNames(body: string): string[] {
  return parseGenUi(body || '').lines.map(({ component }) => component)
}

export function validateOverlayBody(body: string): string {
  for (const { component, argKeys } of parseGenUi(body || '').lines) {
    const refusal = validateInvocation(component, argKeys)
    if (refusal) return refusal.message
  }
  return ''
}

function refuse(doc: SurfaceOverlayDoc, what: string, fix = 'Fix the named component or prop, then reload.'): OverlayRefusalRow {
  return {
    file: doc.file, surface: doc.surface || '',
    error: { code: CODE_OVERLAY_COMPONENT, what,
      why: 'An overlay is a tree of references to already-registered components. A name the host registry does not have, or a prop its schema refuses, is refused whole — a dropped node would be an invisible failure.',
      fix, suggestions: [] },
  }
}

function installDocument(doc: SurfaceOverlayDoc): OverlayRefusalRow | null {
  const source = sourceOf(doc.file)
  let committed = false
  try {
    for (const definition of doc.define ?? []) {
      const result = registerLayerComponent({
        name: definition.name, group: 'Layout', description: definition.description || `A ${doc.file} overlay composite.`,
        args: [], component: () => <GenUiNodes content={definition.body} />,
      }, { layer: LAYER_USER, source })
      if (!result.ok) return refuse(doc, `${doc.file} could not define "${definition.name}": ${result.message}`,
        result.code === 'shadows-core' ? 'Rename the composite — an overlay may ADD component names, never take a core one.'
          : 'Rename the composite so it does not collide with an existing registration.')
    }
    const bodies = [
      ...(doc.define ?? []).map((definition) => ({ prefix: `${doc.file} composite "${definition.name}"`, body: definition.body })),
      { prefix: doc.file, body: doc.body },
    ]
    for (const entry of bodies) {
      const error = validateOverlayBody(entry.body)
      if (error) return refuse(doc, `${entry.prefix}: ${error}`)
    }
    committed = true
    return null
  } catch (error) {
    return refuse(doc, `${doc.file}: ${error instanceof Error ? error.message : String(error)}`)
  } finally {
    if (!committed) removeComponentsFrom(source)
  }
}

class SurfaceCatalog {
  private snapshot: OverlaySnapshot = { overlays: [], refusals: [] }
  private sources = new Set<string>()
  private listeners = new Set<() => void>()
  private generation = 0
  private complete = false
  private pending: Promise<void> | null = null

  read = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private publish(snapshot: OverlaySnapshot) {
    this.snapshot = snapshot
    for (const listener of this.listeners) listener()
  }

  load(): Promise<void> {
    if (maxSurfaceLayer() < LAYER_USER || this.complete) return Promise.resolve()
    if (this.pending) return this.pending
    const generation = this.generation
    const request = this.fetch(generation)
    this.pending = request
    void request.finally(() => { if (this.pending === request) this.pending = null })
    return request
  }

  private async fetch(generation: number): Promise<void> {
    let payload: SurfaceOverlayPayload
    try { payload = await api.surfaceOverlays() }
    catch {
      if (generation === this.generation) this.complete = true
      return
    }
    if (generation !== this.generation || maxSurfaceLayer() < LAYER_USER) return
    const next: OverlaySnapshot = { overlays: [], refusals: (payload.refusals ?? []).map((refusal) => ({ ...refusal, surface: '' })) }
    for (const doc of payload.overlays ?? []) {
      this.sources.add(sourceOf(doc.file))
      const refusal = installDocument(doc)
      if (refusal) {
        next.refusals.push(refusal)
        console.warn(`[surfaces] overlay refused: ${refusal.error.what}`)
      } else next.overlays.push(doc)
    }
    this.complete = true
    this.publish(next)
  }

  reset() {
    this.generation++
    for (const source of this.sources) removeComponentsFrom(source)
    this.sources.clear()
    this.complete = false
    this.pending = null
    this.publish({ overlays: [], refusals: [] })
  }
}
const catalog = new SurfaceCatalog()
export const subscribeSurfaceOverlays = catalog.subscribe
export const surfaceOverlaySnapshot = catalog.read
export const loadSurfaceOverlays = () => catalog.load()
export const resetSurfaceOverlays = () => catalog.reset()
export function overlaysFor(surface: string): SurfaceOverlayDoc[] {
  return maxSurfaceLayer() < LAYER_USER ? [] : catalog.read().overlays.filter((overlay) => overlay.surface === surface)
}
export function overlayRefusalsFor(surface: string): OverlayRefusalRow[] {
  return catalog.read().refusals.filter((refusal) => refusal.surface === surface || (!refusal.surface && surface === REFUSAL_HOME))
}
