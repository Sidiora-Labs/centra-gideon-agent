import { useEffect, useRef, useState } from 'react'
import { Loader2, AlertTriangle } from 'lucide-react'
import { AppApiProvider, loadContributedModule, type AppContext } from '../../app/shell/appSdk'
import { LAYER_APP, maxSurfaceLayer } from '../../shared/ui/surfaces/layers'
import { LayerBoundary } from '../../shared/ui/surfaces/LayerBoundary'
import type { AppHost } from './AppFrame'
import { createRoot, type Root } from 'react-dom/client'
import { createElement } from 'react'


type MountFn =
  | ((el: HTMLElement, ctx: AppContext) => void | (() => void))
  | ((ctx: AppContext) => React.ReactNode)

interface Props {
  app: AppContext
  host?: AppHost
  src: string
  /** Exported mount function name (manifest ui.pages[].mountFunction). */
  mountFunction?: string
}

export function ContributedPage({ app, host, src, mountFunction = 'mount' }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | undefined
    let root: Root | undefined

    setLoading(true); setError(null)
    if (maxSurfaceLayer() < LAYER_APP) {
      setError('Safe mode is on — app-contributed surfaces are not loaded. Reload without ?safe=1 to restore them.')
      setLoading(false)
      return
    }
    const ctx: AppContext = host ? Object.assign(app, { host }) : app
    loadContributedModule(src, ctx)
      .then((mod) => {
        if (cancelled || !hostRef.current) return
        const fn = mod[mountFunction] as MountFn | undefined
        if (typeof fn !== 'function') {
          throw new Error(`app bundle has no "${mountFunction}" export`)
        }
        let node: React.ReactNode | undefined
        try { node = (fn as (ctx: AppContext) => React.ReactNode)(ctx) } catch { node = undefined }
        if (node !== undefined && node !== null) {
          root = createRoot(hostRef.current)
          root.render(createElement(
            LayerBoundary,
            { layer: LAYER_APP, what: app.name, children: createElement(AppApiProvider, { app: ctx, children: node as React.ReactNode }) },
          ))
        } else {
          const ret = (fn as (el: HTMLElement, ctx: AppContext) => void | (() => void))(hostRef.current, ctx)
          if (typeof ret === 'function') cleanup = ret
        }
        setLoading(false)
      })
      .catch((e) => { if (!cancelled) { setError(String(e?.message || e)); setLoading(false) } })

    return () => {
      cancelled = true
      try { cleanup?.() } catch {   }
      try { root?.unmount() } catch {   }
    }
  }, [src, mountFunction, app, host])

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-on-surface-low">
        <AlertTriangle size={22} className="text-warn" />
        <div data-type="body-m">Failed to load {app.name}</div>
        <div data-type="body-s" className="max-w-md text-center opacity-70">{error}</div>
      </div>
    )
  }
  return (
    <div className="relative h-full">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 size={22} className="animate-spin text-on-surface-low" />
        </div>
      )}
      <div ref={hostRef} className="h-full" />
    </div>
  )
}
