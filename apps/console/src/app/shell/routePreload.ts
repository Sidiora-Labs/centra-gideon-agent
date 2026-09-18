import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { parseRouteHash } from './useHashRoute'

type RouteModule<P> = { default: ComponentType<P> }
type PreloadHost = { __gideon_preload_route?: (path: string) => Promise<boolean> }

const loaders = new Map<string, () => Promise<unknown>>()
const started = new Map<string, Promise<boolean>>()

export function lazyRoute<P>(route: string, load: () => Promise<RouteModule<P>>): LazyExoticComponent<ComponentType<P>> {
  loaders.set(route, load)
  return lazy(load)
}

export function preloadRoute(path: string): Promise<boolean> {
  const { route } = parseRouteHash(path, '')
  const load = loaders.get(route)
  if (!load) return Promise.resolve(false)
  let pending = started.get(route)
  if (!pending) {
    pending = load().then(() => true, () => false)
    started.set(route, pending)
  }
  return pending
}

export function installRoutePreload(): void {
  if (typeof window === 'undefined') return
  ;(window as unknown as PreloadHost).__gideon_preload_route = preloadRoute
}
