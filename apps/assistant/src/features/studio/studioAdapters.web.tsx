import { lazy, Suspense } from 'react'
import type { StudioModuleProps } from './studioContracts'
import { studioRecordRef } from './studioContracts'
import { createStudioRoute, studioDestination } from './studioRoutes'

const MediaLibrary = lazy(() => import('../../../../console/src/features/capabilities/media/LibraryPage'))
const WriterWorkspace = lazy(() => import('./WriterWorkspace.web'))

export function hasNativeStudioView(route: StudioModuleProps['route']): boolean {
  const id = studioDestination(route)?.id
  return id === 'capabilities/media/library' || !!id?.startsWith('capabilities/creative/')
}

export function StudioNativeView({ route, scope, navigate, onReturn }: StudioModuleProps) {
  if (!hasNativeStudioView(route)) return null
  if (studioDestination(route)?.id.startsWith('capabilities/creative/')) return <Suspense fallback={<p role="status">Loading Writer…</p>}>
    <WriterWorkspace route={route} scope={scope} navigate={navigate} onReturn={onReturn} />
  </Suspense>
  const artifactId = route.record?.kind === 'media.artifact' ? route.record.id : ''
  return <Suspense fallback={<p role="status">Loading media library…</p>}>
    <MediaLibrary artifactId={artifactId}
      onSelectArtifact={id => navigate(createStudioRoute('capabilities/media/library', route.returnTo,
        id ? studioRecordRef(scope, { kind: 'media.artifact', id }) : undefined, scope))}
      onNavigate={() => navigate(createStudioRoute('capabilities/media/sketches', route.returnTo))} />
  </Suspense>
}
