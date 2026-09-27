import { lazy, Suspense } from 'react'
import type { StudioModuleProps } from './studioContracts'
import { studioRecordRef } from './studioContracts'
import { createStudioRoute, studioDestination } from './studioRoutes'

const MediaLibrary = lazy(() => import('../../../../console/src/features/capabilities/media/LibraryPage'))

export function hasNativeStudioView(route: StudioModuleProps['route']): boolean {
  return studioDestination(route)?.id === 'capabilities/media/library'
}

export function StudioNativeView({ route, scope, navigate }: StudioModuleProps) {
  if (!hasNativeStudioView(route)) return null
  const artifactId = route.record?.kind === 'media.artifact' ? route.record.id : ''
  return <Suspense fallback={<p role="status">Loading media library…</p>}>
    <MediaLibrary artifactId={artifactId}
      onSelectArtifact={id => navigate(createStudioRoute('capabilities/media/library', route.returnTo,
        id ? studioRecordRef(scope, { kind: 'media.artifact', id }) : undefined, scope))}
      onNavigate={() => navigate(createStudioRoute('capabilities/media/sketches', route.returnTo))} />
  </Suspense>
}
