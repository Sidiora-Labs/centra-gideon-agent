import { useCallback, useEffect, useRef, useState } from 'react'
import type { FsEntry } from '../../../shared/data/api'
import { baseName } from '../fileMeta'
import { confirm } from '../../../shared/ui/dialog'

export interface OpenTab { path: string; name: string }

interface StoredTabs {
  key: string
  tabs: OpenTab[]
  activePath: string
}

const DEFAULT_TABS_KEY = 'files-open-tabs'
const MAX_TABS = 12

function readStoredTabs(key: string): StoredTabs {
  let tabs: OpenTab[] = []
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]')
    if (Array.isArray(value)) tabs = value
  } catch {   }
  const storedActivePath = localStorage.getItem(`${key}-active`) || ''
  return {
    key,
    tabs,
    activePath: tabs.some((tab) => tab.path === storedActivePath)
      ? storedActivePath
      : (tabs.at(-1)?.path || ''),
  }
}

export function useFileTabs(scope = '') {
  const tabsKey = scope ? `${DEFAULT_TABS_KEY}:${scope}` : DEFAULT_TABS_KEY
  const activeKey = `${tabsKey}-active`
  const [stored, setStored] = useState<StoredTabs>(() => readStoredTabs(tabsKey))
  const current = stored.key === tabsKey ? stored : readStoredTabs(tabsKey)
  const { tabs, activePath } = current
  const [dirty, setDirty] = useState<Record<string, boolean>>({})
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty

  useEffect(() => {
    setStored((value) => value.key === tabsKey ? value : readStoredTabs(tabsKey))
  }, [tabsKey])
  useEffect(() => { try { localStorage.setItem(tabsKey, JSON.stringify(tabs)) } catch {   } }, [tabs, tabsKey])
  useEffect(() => { localStorage.setItem(activeKey, activePath) }, [activePath, activeKey])

  const anyDirty = Object.values(dirty).some(Boolean)
  useEffect(() => {
    if (!anyDirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [anyDirty])

  const setActivePath = useCallback((path: string) => {
    setStored((value) => {
      const scoped = value.key === tabsKey ? value : readStoredTabs(tabsKey)
      return scoped.activePath === path ? scoped : { ...scoped, activePath: path }
    })
  }, [tabsKey])

  const open = useCallback((entry: FsEntry) => {
    setStored((value) => {
      const scoped = value.key === tabsKey ? value : readStoredTabs(tabsKey)
      if (scoped.tabs.some((t) => t.path === entry.path)) {
        return scoped.activePath === entry.path ? scoped : { ...scoped, activePath: entry.path }
      }
      let next = [...scoped.tabs, { path: entry.path, name: entry.name || baseName(entry.path) }]
      while (next.length > MAX_TABS) {
        const victim = next.find((t) =>
          t.path !== entry.path && t.path !== scoped.activePath && !dirtyRef.current[t.path])
        if (!victim) break
        next = next.filter((t) => t.path !== victim.path)
      }
      return { ...scoped, tabs: next, activePath: entry.path }
    })
  }, [tabsKey])

  const closeNow = useCallback((path: string) => {
    setStored((value) => {
      const scoped = value.key === tabsKey ? value : readStoredTabs(tabsKey)
      const next = scoped.tabs.filter((t) => t.path !== path)
      return {
        ...scoped,
        tabs: next,
        activePath: scoped.activePath === path ? (next.at(-1)?.path || '') : scoped.activePath,
      }
    })
    setDirty((d) => { const n = { ...d }; delete n[path]; return n })
  }, [tabsKey])

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
    setStored((value) => {
      const scoped = value.key === tabsKey ? value : readStoredTabs(tabsKey)
      if (!scoped.tabs.some((t) => moved(t.path) !== t.path)) return scoped
      const next = scoped.tabs.map((t) => {
        const next = moved(t.path)
        return next === t.path ? t : { path: next, name: baseName(next) }
      })
      return { ...scoped, tabs: next, activePath: moved(scoped.activePath) }
    })
    setDirty((d) => {
      const n: Record<string, boolean> = {}
      for (const [p, v] of Object.entries(d)) n[moved(p)] = v
      return n
    })
  }, [tabsKey])

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
