import { useEffect, useRef } from 'react'
import MediaPage from '../../../../console/src/features/capabilities/media/Page'
import type { OwnerScope } from '../../shared/auth.web'
import { serializeShellRoute, type ShellRoute } from '../../shared/shell/shellRoutes'
import { studioRecordRef } from './studioContracts'
import { createStudioRoute, studioRouteRecord, studioTarget } from './studioRoutes'

const destinations: Record<string, string> = {
  sketches: 'capabilities/media/sketches', images: 'capabilities/media/images',
  videos: 'capabilities/media/videos', animation: 'capabilities/media/animations',
  sprites: 'capabilities/media/sprites', episodes: 'capabilities/media/episodes',
  timelines: 'capabilities/media/timelines', cleanup: 'capabilities/media/cleanup',
  datasets: 'capabilities/media/datasets', downloads: 'capabilities/media/downloads',
  library: 'capabilities/media/library', jobs: 'capabilities/media/jobs',
  readiness: 'capabilities/media/readiness',
}

const recordKinds: Record<string, string> = {
  sketches: 'media.sketch', episodes: 'media.episode', timelines: 'media.timeline',
  library: 'media.artifact', jobs: 'media.job', animation: 'media.job',
  sprites: 'media.job', downloads: 'media.job',
}

export default function MediaWorkspace({ route, scope, navigate }: {
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
}) {
  const view = studioTarget(route)?.view
  const routeKey = `${scope.cacheKey}\0${scope.ownerId}\0${scope.runtimeOrigin}\0${serializeShellRoute(route)}`
  const active = useRef({ mounted: false, routeKey })
  active.current.routeKey = routeKey
  useEffect(() => {
    active.current.mounted = true
    return () => { active.current.mounted = false }
  }, [])
  if (!view || !destinations[view]) return <p role="alert">This media workspace is unavailable.</p>
  const record = studioRouteRecord(route, scope)
  function open(nextView: string, id?: string) {
    if (!active.current.mounted || active.current.routeKey !== routeKey) return
    const destination = destinations[nextView]
    if (!destination) return
    const kind = recordKinds[nextView]
    const source = record?.source ?? (route.placement?.query?.sourceConversation || route.placement?.query?.sourceRun
      ? { conversationId: route.placement.query.sourceConversation, runId: route.placement.query.sourceRun } : undefined)
    const ref = id && kind ? studioRecordRef(scope, { kind, id, source }) : undefined
    navigate(createStudioRoute(destination, route.returnTo, ref, scope))
  }
  return <MediaPage key={`${view}:${record?.id ?? ''}`} view={view} recordId={record?.id}
    onNavigate={open} />
}
