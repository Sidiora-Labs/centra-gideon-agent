import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { parseRouteHash } from './useHashRoute'

type RouteModule<P> = { default: ComponentType<P> }
type PreloadHost = { __gideon_preload_route?: (path: string) => Promise<boolean> }

const loaders = new Map<string, () => Promise<unknown>>()

export function lazyRoute<P>(route: string, load: () => Promise<RouteModule<P>>): LazyExoticComponent<ComponentType<P>> {
  let pending: Promise<RouteModule<P>> | undefined
  const loadOnce = () => {
    pending ??= load()
    return pending
  }
  loaders.set(route, loadOnce)
  return lazy(loadOnce)
}

export async function preloadRoute(path: string): Promise<boolean> {
  const { route } = parseRouteHash(path, '')
  const load = loaders.get(route)
  if (!load) return false
  try {
    await load()
    return true
  } catch {
    return false
  }
}

export function installRoutePreload(): void {
  if (typeof window === 'undefined') return
  ;(window as unknown as PreloadHost).__gideon_preload_route = preloadRoute
}
