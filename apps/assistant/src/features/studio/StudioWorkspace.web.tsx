import React, { useEffect, useRef, useState } from 'react'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute, serializeShellRoute } from '../../shared/shell/shellRoutes'
import type { StudioModuleProps } from './studioContracts'
import { resolveStudioRoute, studioDestination } from './studioRoutes'
import { hasNativeStudioView, StudioNativeView } from './studioAdapters.web'

export default function StudioWorkspace({ route, scope, navigate, returnTo, onReturn }: StudioModuleProps) {
  const destination = studioDestination(route)
  const routeKey = `${scope.cacheKey}\0${scope.ownerId}\0${scope.runtimeOrigin}\0${serializeShellRoute(route)}`
  const loading: WorkspaceFrameState = { kind: 'loading', message: 'Checking Studio access…' }
  const [resolution, setResolution] = useState<{ key: string; state: WorkspaceFrameState }>({ key: routeKey, state: loading })
  const generation = useRef(0)
  const [retry, setRetry] = useState(0)
  const state = resolution.key === routeKey ? resolution.state : loading

  useEffect(() => {
    const request = ++generation.current
    setResolution({ key: routeKey, state: loading })
    void resolveStudioRoute(route, scope).then(result => {
      if (generation.current !== request) return
      if (result === 'denied') setResolution({ key: routeKey, state: { kind: 'denied', message: 'You do not have access to this Studio item.' } })
      else if (result === 'missing') setResolution({ key: routeKey, state: { kind: 'empty', message: 'This Studio item is no longer available.' } })
      else if (result === 'unavailable') setResolution({ key: routeKey, state: { kind: 'error', message: 'This Studio workspace cannot be opened right now.', onRetry: () => setRetry(value => value + 1) } })
      else if (hasNativeStudioView(route)) setResolution({ key: routeKey, state: { kind: 'ready' } })
      else setResolution({ key: routeKey, state: { kind: 'empty', message: 'This Studio workspace is unavailable in the assistant right now.' } })
    })
    return () => { generation.current++ }
  }, [routeKey, retry])

  function back() {
    if (returnTo || route.returnTo) onReturn()
    else navigate(createShellRoute('apps'))
  }

  return <WorkspaceFrame route={route} mode="full" title={destination?.label ?? 'Studio'}
    state={state} onBack={back} onGoToChat={() => navigate(createShellRoute('chat'))}>
    {state.kind === 'ready' && <StudioNativeView route={route} scope={scope} navigate={navigate}
      returnTo={returnTo} onReturn={onReturn} />}
  </WorkspaceFrame>
}
