import { useCallback, useEffect, useRef, useState } from 'react'
import { notify } from '../../app/shell/appSdk'

export function useCompanionAction<P extends object>(fetched: unknown) {
  const [patches, setPatches] = useState<Map<string, P>>(new Map())
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const busyRef = useRef(busy)
  busyRef.current = busy

  useEffect(() => {
    if (fetched === undefined) return
    setPatches((m) => {
      const next = new Map([...m].filter(([id]) => busyRef.current.has(id)))
      return next.size === m.size ? m : next
    })
  }, [fetched])

  const act = useCallback(
    async (id: string, patch: P, call: () => Promise<unknown>, what: string, after?: () => void) => {
      setBusy((s) => new Set(s).add(id))
      setPatches((m) => new Map(m).set(id, patch))
      let ok = false
      try {
        await call()
        ok = true
      } catch (e) {
        setPatches((m) => {
          const n = new Map(m)
          n.delete(id)
          return n
        })
        notify(`Couldn't ${what} — ${(e as Error)?.message || 'the gateway did not respond'}`, 'error')
      } finally {
        setBusy((s) => {
          const n = new Set(s)
          n.delete(id)
          return n
        })
        after?.()
      }
      return ok
    },
    [],
  )

  const view = useCallback(<T extends object>(id: string, row: T): T => {
    const patch = patches.get(id)
    return patch ? { ...row, ...patch } : row
  }, [patches])

  return { act, view, busy }
}
