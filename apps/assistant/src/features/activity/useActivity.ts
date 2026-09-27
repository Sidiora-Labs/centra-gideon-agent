import { useEffect, useMemo, useSyncExternalStore } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import {
  ACTIVITY_SOURCES, emptyActivitySnapshot, readActivitySource, withActivitySource,
  type ActivityReadSource, type ActivitySnapshot,
} from './readActivity'

export class ActivityController {
  private scope: OwnerScope | null = null
  private generation = 0
  private snapshot = emptyActivitySnapshot(null)
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): ActivitySnapshot => this.snapshot

  private publish(next: ActivitySnapshot): void {
    this.snapshot = next
    for (const listener of this.listeners) listener()
  }

  setScope(scope: OwnerScope | null): void {
    if (this.scope?.cacheKey === scope?.cacheKey) return
    this.generation++
    this.scope = scope
    this.publish(emptyActivitySnapshot(scope))
  }

  async refresh(): Promise<void> {
    const scope = this.scope
    if (!scope) return
    const generation = ++this.generation
    for (const source of ACTIVITY_SOURCES) {
      const previous = this.snapshot.sources[source]
      this.publish(withActivitySource(this.snapshot, { ...previous, phase: 'loading',
        freshness: previous.freshness === 'current' ? 'stale' : previous.freshness }))
    }
    await Promise.all(ACTIVITY_SOURCES.map(async source => {
      const previous = this.snapshot.sources[source]
      const result = await readActivitySource(scope, source, previous)
      if (generation !== this.generation || this.scope?.cacheKey !== scope.cacheKey) return
      this.publish(withActivitySource(this.snapshot, result))
    }))
  }

  async loadMore(source: ActivityReadSource): Promise<void> {
    const scope = this.scope
    const previous = this.snapshot.sources[source]
    if (!scope || previous.nextOffset === null || previous.phase === 'loading') return
    const generation = this.generation
    this.publish(withActivitySource(this.snapshot, { ...previous, phase: 'loading', freshness: 'stale' }))
    const result = await readActivitySource(scope, source, previous, previous.nextOffset)
    if (generation !== this.generation || this.scope?.cacheKey !== scope.cacheKey) return
    this.publish(withActivitySource(this.snapshot, result))
  }

  returnedFromDetail(): Promise<void> { return this.refresh() }
  reconnected(): Promise<void> { return this.refresh() }
  signOut(): void { this.setScope(null) }
}

export function useActivity(scope: OwnerScope | null): Readonly<{
  snapshot: ActivitySnapshot
  refresh: () => Promise<void>
  loadMore: (source: ActivityReadSource) => Promise<void>
  returnedFromDetail: () => Promise<void>
  reconnected: () => Promise<void>
}> {
  const controller = useMemo(() => new ActivityController(), [])
  useEffect(() => { controller.setScope(scope) }, [controller, scope?.cacheKey])
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const maskedSnapshot = useMemo(() => emptyActivitySnapshot(scope), [scope?.cacheKey])
  const visibleSnapshot = snapshot.ownerScopeKey === (scope?.cacheKey ?? null) ? snapshot : maskedSnapshot
  return { snapshot: visibleSnapshot,
    refresh: () => { controller.setScope(scope); return controller.refresh() },
    loadMore: source => { controller.setScope(scope); return controller.loadMore(source) },
    returnedFromDetail: () => { controller.setScope(scope); return controller.returnedFromDetail() },
    reconnected: () => { controller.setScope(scope); return controller.reconnected() } }
}
