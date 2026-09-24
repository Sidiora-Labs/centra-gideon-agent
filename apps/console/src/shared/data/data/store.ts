import { staleAfterMsFor } from './keys'

export interface CacheEntry<T = unknown> {
  value: T
  at: number
  epoch: number
}
export type CacheKeySpec = string | { prefix: string }

type QueryChannel = {
  entry?: CacheEntry
  request?: Promise<unknown>
  revision: number
  persisted: boolean
  listeners: Set<() => void>
}

const channels = new Map<string, QueryChannel>()
const storagePrefix = 'cache:'
export const READER_WAIT_MS = 8_000

function channelFor(key: string): QueryChannel {
  let channel = channels.get(key)
  if (!channel) {
    channel = { revision: 0, persisted: false, listeners: new Set() }
    channels.set(key, channel)
  }
  return channel
}

function publish(channel: QueryChannel): void {
  for (const listener of [...channel.listeners]) listener()
}

function sessionRecord(key: string): CacheEntry | undefined {
  try {
    const raw = sessionStorage.getItem(storagePrefix + key)
    if (raw === null) return undefined
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || !('v' in parsed)) return undefined
    return { value: parsed.v, at: 0, epoch: 0 }
  } catch { return undefined }
}

function saveSession(key: string, entry?: CacheEntry): void {
  try {
    if (entry) sessionStorage.setItem(storagePrefix + key, JSON.stringify({ v: entry.value, at: entry.at }))
    else sessionStorage.removeItem(storagePrefix + key)
  } catch { /* The in-memory cache remains available when storage is denied. */ }
}

function commit(key: string, channel: QueryChannel, value: unknown): void {
  channel.entry = { value, at: Date.now(), epoch: channel.entry?.epoch ?? 0 }
  if (channel.persisted) saveSession(key, channel.entry)
  publish(channel)
}

export function readEntry<T>(key: string, persist = false): CacheEntry<T> | undefined {
  const channel = channelFor(key)
  if (persist) {
    channel.persisted = true
    channel.entry ??= sessionRecord(key)
  }
  return channel.entry as CacheEntry<T> | undefined
}

export function isStale(key: string, entry: CacheEntry | undefined, staleAfterMs?: number): boolean {
  return !!entry && (entry.at === 0 || Date.now() - entry.at > (staleAfterMs ?? staleAfterMsFor(key)))
}

export function writeQuery(key: string, value: unknown, persist = false): void {
  const channel = channelFor(key)
  channel.revision += 1
  channel.persisted ||= persist
  commit(key, channel, value)
}

export function peekQuery<T>(key: string): T | undefined {
  const entry = readEntry<T>(key, true)
  return isStale(key, entry) ? undefined : entry?.value
}

export const peekEntry = <T,>(key: string): CacheEntry<T> | undefined => readEntry<T>(key, true)

export function invalidateKeys(keyOrPrefix: string, prefix = false): void {
  const targets = prefix
    ? [...channels.keys()].filter((key) => key.startsWith(keyOrPrefix))
    : [keyOrPrefix]
  for (const key of targets) {
    const channel = channelFor(key)
    channel.revision += 1
    channel.request = undefined
    channel.entry = { value: channel.entry?.value, at: 0, epoch: (channel.entry?.epoch ?? 0) + 1 }
    saveSession(key)
    publish(channel)
  }
}

export function invalidateSpecs(specs: readonly CacheKeySpec[]): void {
  for (const spec of specs) {
    if (typeof spec === 'string') invalidateKeys(spec)
    else invalidateKeys(spec.prefix, true)
  }
}

export function fetchKey<T>(key: string, fetcher: () => Promise<T>, persist = false): Promise<T> {
  const channel = channelFor(key)
  channel.persisted ||= persist
  if (channel.request) return channel.request as Promise<T>
  const revision = channel.revision
  let source: Promise<T>
  try { source = Promise.resolve(fetcher()) } catch (error) { source = Promise.reject(error) }
  let timer: ReturnType<typeof setTimeout>
  const ceiling = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error('Data request timed out after 8 seconds. Try again.')), READER_WAIT_MS)
  })
  const request = Promise.race([source, ceiling]).then((value) => {
    if (channels.get(key) === channel && channel.revision === revision) commit(key, channel, value)
    return value
  }).finally(() => {
    clearTimeout(timer)
    if (channel.request === request) channel.request = undefined
  })
  channel.request = request
  return request
}

export const isFetching = (key: string): boolean => channels.get(key)?.request !== undefined

export function subscribeKey(key: string, listener: () => void): () => void {
  const channel = channelFor(key)
  channel.listeners.add(listener)
  return () => { channel.listeners.delete(listener) }
}

export function resetDataStore(): void {
  channels.clear()
  try {
    for (const key of Object.keys(sessionStorage)) {
      if (key.startsWith(storagePrefix)) sessionStorage.removeItem(key)
    }
  } catch { /* Storage is optional. */ }
}

export function cachedKeys(): string[] {
  return [...channels].filter(([, channel]) => channel.entry !== undefined).map(([key]) => key)
}
