import { Loader2, Blocks } from 'lucide-react'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, ApiError } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { EmptyState, LoadError, LoadingStatus } from '../../shared/ui/ListScaffold'
import { AppFrame } from './AppFrame'
import type { AppContext, AppPermissions } from '../../app/shell/appSdk'

interface UIPageDecl { route?: string; label?: string; entryPoint?: string; mountFunction?: string }

export function AppHostPage({ sub, navigate }: Pick<RouteProps, 'sub' | 'navigate'>) {
  const name = sub.split('/')[0]
  const { data, error, refresh } = useQuery(`app-host:${name}`, () => api.app(name), { persist: false })

  if (!name) return <Center>No app specified</Center>
  if (error) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="flex h-full items-center justify-center">
          <EmptyState icon={Blocks} title={`“${name}” isn’t installed`}
            hint="Install it from the Store to open it here."
            action={{ label: 'Open the Store', onClick: () => navigate('apps?view=store') }} />
        </div>
      )
    }
    return (
      <div className="flex h-full items-center justify-center">
        <LoadError what="app" error={error} onRetry={() => { invalidateKeys(`app-host:${name}`); refresh() }} />
      </div>
    )
  }
  if (data === undefined) return <Center spinner />

  const manifest = (data.manifest ?? {}) as Record<string, unknown>
  const ui = (manifest.ui ?? {}) as { pages?: UIPageDecl[] }
  const page = ui.pages?.find((p) => p.entryPoint)
  if (!page?.entryPoint) return <Center><Blocks size={20} /> This app contributes no UI page.</Center>

  const permissions = (manifest.permissions ?? {}) as AppPermissions
  const uiCapabilities = (manifest.uiCapabilities ?? []) as string[]
  const ctx: AppContext = { name, permissions, uiCapabilities }
  const src = `/apps/${encodeURIComponent(name)}/ui/${page.entryPoint}`
  const title = page.label || (manifest.displayName as string) || name
  const icon = (page as { icon?: string }).icon || (manifest.icon as string) || ''
  return <AppFrame app={ctx} title={title} icon={icon} src={src} mountFunction={page.mountFunction || 'mount'} />
}

function Center({ children, spinner }: { children?: React.ReactNode; spinner?: boolean }) {
  if (spinner) {
    return (
      <div role="status" aria-busy="true"
        className="flex h-full items-center justify-center gap-2 text-on-surface-low" data-type="body-m">
        <LoadingStatus what="the app" />
        <Loader2 size={22} className="animate-spin" />
      </div>
    )
  }
  return (
    <div className="flex h-full items-center justify-center gap-2 text-on-surface-low" data-type="body-m">
      {children}
    </div>
  )
}
