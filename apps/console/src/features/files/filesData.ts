import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type FsEntry, type FsRoot } from '../../shared/data/api'

export function useFileRoots() {
  const [roots, setRoots] = useState<FsRoot[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    api.fileRoots().then((r) => { if (alive) { setRoots(r.roots); setLoading(false) } }).catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])
  return { roots, loading }
}

const DIR_CACHE_KEY = 'files-dir-cache'
const DIR_CACHE_MAX_PATHS = 400

function loadPersistedCache(): Record<string, FsEntry[]> {
  try {
    const raw = sessionStorage.getItem(DIR_CACHE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, FsEntry[]> : {}
  } catch { return {} }
}

function persistCache(cache: Record<string, FsEntry[]>): void {
  try {
    const paths = Object.keys(cache)
    const trimmed = paths.length > DIR_CACHE_MAX_PATHS
      ? Object.fromEntries(paths.slice(paths.length - DIR_CACHE_MAX_PATHS).map((p) => [p, cache[p]]))
      : cache
    sessionStorage.setItem(DIR_CACHE_KEY, JSON.stringify(trimmed))
  } catch {   }
}

export function useDirCache() {
  const [cache, setCache] = useState<Record<string, FsEntry[]>>(loadPersistedCache)
  const [resolved, setResolved] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const inflight = useRef<Record<string, boolean>>({})
  const cacheRef = useRef(cache)
  cacheRef.current = cache
  useEffect(() => { persistCache(cache) }, [cache])
  const gen = useRef<Record<string, number>>({})

  const load = useCallback(async (path: string, force = false): Promise<FsEntry[]> => {
    const cached = cacheRef.current[path]
    if (!force && cached) return cached
    if (inflight.current[path]) return cached ?? []
    inflight.current[path] = true
    const startGen = gen.current[path] ?? 0
    try {
      const r = await api.fileList(path)
      if ((gen.current[path] ?? 0) === startGen) {
        setCache((c) => ({ ...c, [path]: r.entries }))
        setResolved((current) => ({ ...current, [path]: r.path }))
        setErrors((current) => { const next = { ...current }; delete next[path]; return next })
      }
      return r.entries
    } catch (failure) {
      if ((gen.current[path] ?? 0) === startGen) {
        setErrors((current) => ({ ...current, [path]: (failure as Error)?.message || 'The server refused this path.' }))
      }
      return []
    } finally {
      inflight.current[path] = false
    }
  }, [])

  const bumpGen = (path: string) => { gen.current[path] = (gen.current[path] ?? 0) + 1 }

  const invalidate = useCallback((path: string) => {
    bumpGen(path)
    setCache((c) => { const next = { ...c }; delete next[path]; return next })
    setResolved((c) => { const next = { ...c }; delete next[path]; return next })
    setErrors((c) => { const next = { ...c }; delete next[path]; return next })
  }, [])

  const invalidateSubtree = useCallback((root: string) => {
    const r = root.replace(/\/$/, '')
    setCache((c) => {
      const next: Record<string, FsEntry[]> = {}
      for (const [k, v] of Object.entries(c)) {
        if (k === r || k.startsWith(r + '/')) continue
        next[k] = v
      }
      return next
    })
    bumpGen(r)
    for (const k of Object.keys(gen.current)) if (k === r || k.startsWith(r + '/')) bumpGen(k)
    for (const k of Object.keys(cacheRef.current)) if (k === r || k.startsWith(r + '/')) bumpGen(k)
  }, [])

  return useMemo(() => ({ cache, resolved, errors, load, invalidate, invalidateSubtree }), [cache, resolved, errors, load, invalidate, invalidateSubtree])
}

export function useGitStatus(rootPath: string | null, nonce = 0) {
  const [branch, setBranch] = useState('')
  const [statuses, setStatuses] = useState<Record<string, string>>({})
  const [state, setState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle')
  const [repoRoot, setRepoRoot] = useState('')
  useEffect(() => {
    if (!rootPath) { setBranch(''); setStatuses({}); setRepoRoot(''); setState('idle'); return }
    let alive = true
    setState('loading')
    api.fileGitStatus(rootPath).then((r) => {
      if (!alive) return
      setBranch(r.branch || '')
      setStatuses(r.statuses || {})
      setRepoRoot(r.repoRoot || '')
      setState('loaded')
    }).catch(() => { if (alive) { setBranch(''); setStatuses({}); setRepoRoot(''); setState('error') } })
    return () => { alive = false }
  }, [rootPath, nonce])
  return { branch, statuses, state, repoRoot }
}
