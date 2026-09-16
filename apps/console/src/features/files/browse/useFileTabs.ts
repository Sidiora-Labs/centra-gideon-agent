import { useCallback, useEffect, useRef, useState } from 'react'
import type { FsEntry } from '../../../shared/data/api'
import { baseName } from '../fileMeta'
import { confirm } from '../../../shared/ui/dialog'

export interface OpenTab { path: string; name: string }

const DEFAULT_TABS_KEY = 'files-open-tabs'
const MAX_TABS = 12

export function useFileTabs(scope = '') {
  const tabsKey = scope ? `${DEFAULT_TABS_KEY}:${scope}` : DEFAULT_TABS_KEY
  const activeKey = `${tabsKey}-active`
  const [tabs, setTabs] = useState<OpenTab[]>(() => {
    try { const v = JSON.parse(localStorage.getItem(tabsKey) || '[]'); return Array.isArray(v) ? v : [] } catch { return [] }
  })
  const [activePath, setActivePath] = useState<string>(() => localStorage.getItem(activeKey) || '')
  const [dirty, setDirty] = useState<Record<string, boolean>>({})
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty
  const activePathRef = useRef(activePath); activePathRef.current = activePath

  useEffect(() => { try { localStorage.setItem(tabsKey, JSON.stringify(tabs)) } catch {   } }, [tabs, tabsKey])
  useEffect(() => { localStorage.setItem(activeKey, activePath) }, [activePath, activeKey])

  const anyDirty = Object.values(dirty).some(Boolean)
  useEffect(() => {
    if (!anyDirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [anyDirty])

  const open = useCallback((entry: FsEntry) => {
    setTabs((prev) => {
      if (prev.some((t) => t.path === entry.path)) return prev
      let next = [...prev, { path: entry.path, name: entry.name || baseName(entry.path) }]
      while (next.length > MAX_TABS) {
        const victim = next.find((t) =>
          t.path !== entry.path && t.path !== activePathRef.current && !dirtyRef.current[t.path])
        if (!victim) break
        next = next.filter((t) => t.path !== victim.path)
      }
      return next
    })
    setActivePath(entry.path)
  }, [])

  const closeNow = useCallback((path: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.path !== path)
      setActivePath((cur) => cur === path ? (next.length ? next[next.length - 1].path : '') : cur)
      return next
    })
    setDirty((d) => { const n = { ...d }; delete n[path]; return n })
  }, [])

  const close = useCallback(async (path: string) => {
    if (dirtyRef.current[path] && !(await confirm({ title: `Discard unsaved changes to ${baseName(path)}?`, body: 'Your edits will be lost.', danger: true, confirmLabel: 'Discard' }))) return false
    closeNow(path)
    return true
  }, [closeNow])

  const markDirty = useCallback((path: string, isDirty: boolean) => {
    setDirty((d) => (d[path] === isDirty ? d : { ...d, [path]: isDirty }))
  }, [])

  const renamePath = useCallback((from: string, to: string) => {
    if (!from || !to || from === to) return
    const moved = (p: string) => (p === from ? to : p.startsWith(`${from}/`) ? to + p.slice(from.length) : p)
    setTabs((prev) => {
      if (!prev.some((t) => moved(t.path) !== t.path)) return prev
      return prev.map((t) => {
        const next = moved(t.path)
        return next === t.path ? t : { path: next, name: baseName(next) }
      })
    })
    setActivePath((cur) => moved(cur))
    setDirty((d) => {
      const n: Record<string, boolean> = {}
      for (const [p, v] of Object.entries(d)) n[moved(p)] = v
      return n
    })
  }, [])

  const tabsUnder = useCallback(
    (path: string) => tabs.filter((t) => t.path === path || t.path.startsWith(`${path}/`)),
    [tabs],
  )

  const active = tabs.find((t) => t.path === activePath) ?? null
  return {
    tabs, active, activePath, dirty, open, close, closeNow, setActivePath, markDirty,
    renamePath, tabsUnder,
  }
}
