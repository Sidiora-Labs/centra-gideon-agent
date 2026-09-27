import { lazy, Suspense } from 'react'
import type { StudioModuleProps } from './studioContracts'
import { studioRecordRef } from './studioContracts'
import { createSlidesRoute, isSlidesRoute, studioDestination } from './studioRoutes'

const WriterWorkspace = lazy(() => import('./WriterWorkspace.web'))
const SlidesWorkspace = lazy(() => import('./SlidesWorkspace.web'))
const MediaWorkspace = lazy(() => import('./MediaWorkspace.web'))

export function hasNativeStudioView(route: StudioModuleProps['route']): boolean {
  const id = studioDestination(route)?.id
  return (id === 'design' && !route.placement?.subview) || isSlidesRoute(route)
    || !!id?.startsWith('capabilities/media/') || !!id?.startsWith('capabilities/creative/')
}

export function StudioNativeView({ route, scope, navigate, onReturn }: StudioModuleProps) {
  if (!hasNativeStudioView(route)) return null
  if (studioDestination(route)?.id === 'design' && !isSlidesRoute(route)) return <section className="flex h-full flex-col gap-4 bg-surface p-4 text-on-surface">
    <div><h2 className="text-xl font-semibold">Design</h2><p className="text-sm text-on-surface-var">Create and edit presentations in Slides.</p></div>
    <div className="rounded-lg border border-outline/40 p-4"><h3 className="font-medium">Slides</h3>
      <p className="text-sm text-on-surface-var">Turn an outline into a presentation, then edit, preview and export its slides.</p>
      <button type="button" onClick={() => navigate(createSlidesRoute(route.returnTo))}>Open Slides</button>
    </div>
  </section>
  if (isSlidesRoute(route)) return <Suspense fallback={<p role="status">Loading Slides…</p>}>
    <SlidesWorkspace scope={scope} artifactId={route.record?.kind === 'artifact' ? route.record.id : ''}
      onSelectArtifact={slug => navigate(createSlidesRoute(route.returnTo,
        slug ? studioRecordRef(scope, { kind: 'artifact', id: slug }) : undefined, scope))} />
  </Suspense>
  if (studioDestination(route)?.id.startsWith('capabilities/media/')) return <Suspense fallback={<p role="status">Loading Media…</p>}>
    <MediaWorkspace route={route} scope={scope} navigate={navigate} />
  </Suspense>
  if (studioDestination(route)?.id.startsWith('capabilities/creative/')) return <Suspense fallback={<p role="status">Loading Writer…</p>}>
    <WriterWorkspace route={route} scope={scope} navigate={navigate} onReturn={onReturn} />
  </Suspense>
  return null
}
