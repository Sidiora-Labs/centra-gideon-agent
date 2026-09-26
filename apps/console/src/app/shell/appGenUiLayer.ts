import { api, type AppSummary } from '../../shared/data/api'
import { loadContributedModule, registerAppGenUiComponent, unregisterAppGenUiComponents, type AppContext } from './appSdk'
import { LAYER_APP, maxSurfaceLayer } from '../../shared/ui/surfaces/layers'
import { bindDonorUISpec, type LiveUISpec } from '../../shared/ui/assistant-ui/generative/uispec'

export interface GenUiRegistrarSdk { registerComponent: typeof registerAppGenUiComponent }
export function contributesComponents(app: Pick<AppSummary, 'enabled' | 'uiComponents' | 'uiCapabilities'>): boolean {
  return Boolean(app.enabled && app.uiComponents?.trim() && app.uiCapabilities?.includes('generative-component'))
}
export function componentsModuleUrl(name: string, module: string): string {
  return `/apps/${encodeURIComponent(name)}/ui/${module.replace(/^\/+/, '')}`
}
interface ComponentLoad { signature: string; count: number }
class ComponentCatalog {
  private entries = new Map<string, ComponentLoad>()
  private available() { return maxSurfaceLayer() >= LAYER_APP }
  private signature(app: AppSummary) { return JSON.stringify([app.version, app.uiComponents, app.uiCapabilities]) }
  remove(name: string) {
    if (!this.entries.delete(name)) return
    unregisterAppGenUiComponents(name)
  }
  clear() { [...this.entries.keys()].forEach((name) => this.remove(name)) }
  async load(app: AppSummary): Promise<number> {
    if (!this.available() || !contributesComponents(app)) return 0
    const signature = this.signature(app)
    if (this.entries.get(app.name)?.signature === signature) return 0
    this.remove(app.name)
    const entry: ComponentLoad = { signature, count: 0 }
    this.entries.set(app.name, entry)
    const current = () => this.available() && this.entries.get(app.name) === entry
    const context: AppContext = { name: app.name, permissions: {}, uiCapabilities: app.uiCapabilities ?? [] }
    const sdk: GenUiRegistrarSdk = {
      registerComponent: (_identity, definition) => {
        if (!current()) return { ok: false, code: 'invalid', message: 'The component registration is no longer active.' }
        const result = registerAppGenUiComponent(context, definition)
        if (result.ok) entry.count += 1
        else console.warn(`[surfaces] app "${app.name}" component refused: ${result.message}`)
        return result
      },
    }
    try {
      const module = await loadContributedModule(componentsModuleUrl(app.name, app.uiComponents ?? ''), context)
      if (!current()) return 0
      if (typeof module.register !== 'function') {
        console.warn(`[surfaces] app "${app.name}" components module has no register() export`)
        return 0
      }
      await module.register(sdk, context)
      return current() ? entry.count : 0
    } catch (error) {
      console.error(`[surfaces] app "${app.name}" components module failed to load`, error)
      if (this.entries.get(app.name) === entry) this.remove(app.name)
      return 0
    }
  }
  async sync(known?: AppSummary[]): Promise<number> {
    if (!this.available()) return 0
    let apps: AppSummary[]
    try { apps = known ?? await api.apps() } catch { return 0 }
    const eligible = new Map(apps.filter(contributesComponents).map((app) => [app.name, app]))
    for (const name of this.entries.keys()) if (!eligible.has(name)) this.remove(name)
    let registered = 0
    for (const app of eligible.values()) registered += await this.load(app)
    return registered
  }
}
const catalog = new ComponentCatalog()
export const loadAppComponents = (app: AppSummary) => catalog.load(app)
export const syncAppGenUiComponents = (known?: AppSummary[]) => catalog.sync(known)
export const resetAppGenUiLayer = () => catalog.clear()

export function resolveAppUISpec(app: Pick<AppSummary, 'name' | 'enabled' | 'uiComponents' | 'uiCapabilities'>,
  payload: unknown): LiveUISpec | null {
  if (maxSurfaceLayer() < LAYER_APP || !contributesComponents(app) || !payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const data = payload as Record<string, unknown>
  if (data.schemaVersion !== 1) return null
  return bindDonorUISpec({ template: data.template, producer: `app:${app.name}`, recordId: data.recordId, bindings: data.bindings })
}
