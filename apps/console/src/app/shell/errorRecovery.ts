export const RELOAD_GUARD_KEY = 'gideon:chunk-reload-at'
const failures = ['failed to fetch dynamically imported module', 'error loading dynamically imported module', 'importing a module script failed', 'chunkloaderror']
export function isChunkLoadError(error: unknown): boolean {
  const value = error as { name?: unknown; message?: unknown } | null
  const text = `${value?.name ?? ''} ${value?.message ?? ''}`.toLowerCase()
  return failures.some((failure) => text.includes(failure))
}
export function canReloadChunk(now: number, previous: string | null): boolean {
  return now - (Number(previous) || 0) >= 10_000
}
