import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { GatewaySocket } from '../../../../console/src/shared/data/socketTransport'
import type { OwnerScope } from '../../shared/auth.web'
import { ownedWebSocketUrl } from '../delivery/resources.web'
import { ActivityEventWindow } from './activityEvents'
import {
  ACTIVITY_SOURCES, emptyActivitySnapshot, readActivitySource, withActivitySource,
  type ActivityReadSource, type ActivitySnapshot,
} from './readActivity'

export const ACTIVITY_RECONCILE_INTERVAL_MS = 15_000

export class ActivityController {
  private scope: OwnerScope | null = null
  private generation = 0
  private refreshTask: Promise<void> | null = null
  private refreshTaskScopeKey: string | null = null
  private refreshQueued = false
  private liveDisconnected = false
  private snapshot = emptyActivitySnapshot(null)
  private listeners = new Set<() => void>()

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): ActivitySnapshot => this.snapshot

  ownsScope = (cacheKey: string): boolean => this.scope?.cacheKey === cacheKey

  private publish(next: ActivitySnapshot): void {
    this.snapshot = next
    for (const listener of this.listeners) listener()
  }

  setScope(scope: OwnerScope | null): void {
    if (this.scope?.cacheKey === scope?.cacheKey) return
    this.generation++
    this.scope = scope
    this.liveDisconnected = false
    this.refreshQueued = false
    this.refreshTask = null
    this.refreshTaskScopeKey = null
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
      const visible = this.liveDisconnected && result.coverage !== 'no_read_route'
        ? { ...result, freshness: 'stale' as const,
          error: 'Live Activity updates are disconnected. Reconnecting before refreshing canonical records.' }
        : result
      this.publish(withActivitySource(this.snapshot, visible))
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
    const visible = this.liveDisconnected && result.coverage !== 'no_read_route'
      ? { ...result, freshness: 'stale' as const,
        error: 'Live Activity updates are disconnected. Reconnecting before refreshing canonical records.' }
      : result
    this.publish(withActivitySource(this.snapshot, visible))
  }

  markDisconnected(cacheKey: string): void {
    if (!this.ownsScope(cacheKey)) return
    this.liveDisconnected = true
    this.generation++
    let next = this.snapshot
    for (const source of ACTIVITY_SOURCES) {
      const previous = next.sources[source]
      next = withActivitySource(next, { ...previous,
        freshness: previous.coverage === 'no_read_route' ? previous.freshness : 'stale',
        error: 'Live Activity updates are disconnected. Reconnecting before refreshing canonical records.' })
    }
    this.publish(next)
  }

  markConnected(cacheKey: string): void {
    if (!this.ownsScope(cacheKey)) return
    this.liveDisconnected = false
  }

  async refreshForScope(cacheKey: string): Promise<void> {
    if (!this.ownsScope(cacheKey)) return
    if (this.refreshTask && this.refreshTaskScopeKey === cacheKey) {
      this.refreshQueued = true
      return this.refreshTask
    }
    do {
      this.refreshQueued = false
      const task = this.refresh()
      this.refreshTask = task
      this.refreshTaskScopeKey = cacheKey
      try { await task } finally {
        if (this.refreshTask === task) {
          this.refreshTask = null
          this.refreshTaskScopeKey = null
        }
      }
    } while (this.refreshQueued && this.ownsScope(cacheKey))
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
  const currentScopeKey = useRef(scope?.cacheKey ?? null)
  currentScopeKey.current = scope?.cacheKey ?? null
  useEffect(() => {
    controller.setScope(scope)
    if (!scope || typeof window === 'undefined') return
    const cacheKey = scope.cacheKey
    const events = new ActivityEventWindow()
    const socket = new GatewaySocket(ownedWebSocketUrl())
    const detach = socket.attach({
      message: message => {
        if (currentScopeKey.current !== cacheKey || !controller.ownsScope(cacheKey)
          || !events.accept(message)) return
        void controller.refreshForScope(cacheKey)
      },
      status: connected => {
        if (currentScopeKey.current !== cacheKey) return
        if (connected) controller.markConnected(cacheKey)
        else controller.markDisconnected(cacheKey)
      },
      reconnect: () => {
        if (currentScopeKey.current === cacheKey) void controller.refreshForScope(cacheKey)
      },
    })
    const reconciliation = window.setInterval(() => {
      if (currentScopeKey.current === cacheKey) void controller.refreshForScope(cacheKey)
    }, ACTIVITY_RECONCILE_INTERVAL_MS)
    return () => { window.clearInterval(reconciliation); detach() }
  }, [controller, scope?.cacheKey])
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
  const maskedSnapshot = useMemo(() => emptyActivitySnapshot(scope), [scope?.cacheKey])
  const visibleSnapshot = snapshot.ownerScopeKey === (scope?.cacheKey ?? null) ? snapshot : maskedSnapshot
  return { snapshot: visibleSnapshot,
    refresh: () => scope && currentScopeKey.current === scope.cacheKey
      ? (controller.setScope(scope), controller.refreshForScope(scope.cacheKey)) : Promise.resolve(),
    loadMore: source => scope && currentScopeKey.current === scope.cacheKey
      ? (controller.setScope(scope), controller.loadMore(source)) : Promise.resolve(),
    returnedFromDetail: () => scope && currentScopeKey.current === scope.cacheKey
      ? (controller.setScope(scope), controller.refreshForScope(scope.cacheKey)) : Promise.resolve(),
    reconnected: () => scope && currentScopeKey.current === scope.cacheKey
      ? (controller.setScope(scope), controller.refreshForScope(scope.cacheKey)) : Promise.resolve() }
}
