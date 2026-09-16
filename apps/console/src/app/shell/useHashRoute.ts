import { useState, useSyncExternalStore } from 'react'
import { flushSync } from 'react-dom'
import { viewTransition } from '../../shared/theme/motion'

export interface RouteSnapshot {
  route: string
  sub: string
  query: Record<string, string>
  navEpoch: number
}
type NavigationOptions = { replace?: boolean }
type QueryPatch = Record<string, string | null | undefined>

const stripHash = (value: string) => value.replace(/^#?\/?/, '')
const liveHash = () => typeof location === 'undefined' ? '' : stripHash(location.hash)

export function parseRouteHash(hash: string, fallback: string, navEpoch = 0): RouteSnapshot {
  const source = stripHash(hash)
  const delimiter = source.indexOf('?')
  const path = delimiter < 0 ? source : source.slice(0, delimiter)
  const search = delimiter < 0 ? '' : source.slice(delimiter + 1)
  const segments = path.split('/').filter(Boolean).map((segment) => {
    try { return decodeURIComponent(segment) } catch { return segment }
  })
  return { route: segments[0] || fallback, sub: segments.slice(1).join('/'), query: Object.fromEntries(new URLSearchParams(search)), navEpoch }
}

export function patchRouteQuery(hash: string, patch: QueryPatch, fallback: string): string {
  const source = stripHash(hash)
  const delimiter = source.indexOf('?')
  const path = (delimiter < 0 ? source : source.slice(0, delimiter)) || fallback
  const search = new URLSearchParams(delimiter < 0 ? '' : source.slice(delimiter + 1))
  for (const [key, value] of Object.entries(patch)) {
    if (value == null || value === '') search.delete(key)
    else search.set(key, value)
  }
  const query = search.toString()
  return query ? `${path}?${query}` : path
}

class HashNavigation {
  private state: RouteSnapshot
  private listeners = new Set<() => void>()

  constructor(private fallback: string) {
    this.state = parseRouteHash(liveHash(), fallback)
  }

  snapshot = (): RouteSnapshot => this.state

  private commit = (navEpoch = this.state.navEpoch): void => {
    this.state = parseRouteHash(liveHash(), this.fallback, navEpoch)
    for (const listener of [...this.listeners]) listener()
  }

  private onHash = (): void => {
    const next = parseRouteHash(liveHash(), this.fallback)
    if (next.route === this.state.route) this.commit()
    else viewTransition(() => { flushSync(() => this.commit()) })
  }

  subscribe = (listener: () => void): (() => void) => {
    if (!this.listeners.size) {
      window.addEventListener('hashchange', this.onHash)
      if (!location.hash) location.replace(`#/${this.fallback}`)
    }
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
      if (!this.listeners.size) window.removeEventListener('hashchange', this.onHash)
    }
  }

  private apply(path: string, options: NavigationOptions | undefined, navigate: boolean): void {
    const epoch = this.state.navEpoch + Number(navigate)
    if (path === liveHash()) {
      this.commit(epoch)
    } else if (options?.replace) {
      history.replaceState(null, '', `#/${path}`)
      this.commit(epoch)
    } else {
      this.state = { ...this.state, navEpoch: epoch }
      location.hash = `#/${path}`
    }
  }

  navigate = (path: string, options?: NavigationOptions): void => {
    this.apply(stripHash(path), options, true)
  }

  setQuery = (patch: QueryPatch, options?: NavigationOptions): void => {
    this.apply(patchRouteQuery(liveHash(), patch, this.fallback), options, false)
  }
}

export function useHashRoute(fallback: string) {
  const [navigation] = useState(() => new HashNavigation(fallback))
  const state = useSyncExternalStore(navigation.subscribe, navigation.snapshot, navigation.snapshot)
  return { ...state, navigate: navigation.navigate, setQuery: navigation.setQuery }
}
