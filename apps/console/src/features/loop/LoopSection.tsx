import { LoopComposer } from './LoopComposer'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { api } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'
import type { RouteProps } from '../../app/shell/useQueryState'
import type { CreatedLoopRun, Loop, LoopKind } from '../../shared/data/api'
import { createdLoopRoute, isCreatedLoopRun } from './creation'

export function LoopSection({ navigate, query }: RouteProps) {
  const routeCreated = async (created: Loop | CreatedLoopRun, planning: boolean) => {
    if (isCreatedLoopRun(created)) {
      invalidateKeys('workflows')
      navigate(createdLoopRoute(created))
      return
    }
    const loopId = created.id
    invalidateKeys('loops')
    if (!planning) await reportingWrite('start this loop', () => api.uLoopAction(loopId, 'start'))
    else await reportingWrite('start planning for this loop', () => api.uLoopPlanStart(loopId))
    navigate(createdLoopRoute(created))
  }
  const kindParam = query.kind as LoopKind | undefined
  const validKind = kindParam && ['general', 'goal', 'code', 'design', 'research'].includes(kindParam) ? kindParam : undefined
  return <LoopComposer onCreated={routeCreated} onHistory={() => navigate('loops/history')}
    initialProjectId={query.project || ''} initialKind={validKind} initialWorkspace={query.ws || ''} />
}
