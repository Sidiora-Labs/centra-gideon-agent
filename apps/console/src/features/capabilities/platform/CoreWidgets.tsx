import type { RouteProps } from '../../../app/shell/useQueryState'
import type { CoreTile } from './Composition'
import { HeroPulse } from '../../dashboard/widgets/HeroPulse'
import { ActionCenter } from '../../dashboard/widgets/ActionCenter'
import { ActiveWork } from '../../dashboard/widgets/ActiveWork'
import { TasksWidget } from '../../dashboard/widgets/TasksWidget'
import { Suggestions } from '../../dashboard/widgets/Suggestions'
import { Discover } from '../../dashboard/widgets/Discover'
import { ScheduleWidget } from '../../dashboard/widgets/ScheduleWidget'
import { SystemHealth } from '../../dashboard/widgets/SystemHealth'
import { AgentWorld } from '../../dashboard/world/AgentWorld'
import { PinnedArtifacts } from '../../dashboard/widgets/PinnedArtifacts'
import { OnThisMachine } from '../../dashboard/widgets/OnThisMachine'
import { DesktopLiveView } from '../../dashboard/widgets/DesktopLiveView'
export function CoreWidgets({ tiles, route }: { tiles: CoreTile[]; route: RouteProps }) {
  const widgets = {
    'core:hero-pulse': <HeroPulse {...route} />, 'core:action-center': <ActionCenter {...route} />, 'core:active-work': <ActiveWork {...route} />, 'core:tasks': <TasksWidget {...route} />, 'core:suggestions': <Suggestions {...route} />, 'core:discover': <Discover {...route} />, 'core:schedule': <ScheduleWidget {...route} />, 'core:system-health': <SystemHealth {...route} />, 'core:agent-world': <AgentWorld />, 'core:pinned-artifacts': <PinnedArtifacts {...route} />, 'core:on-this-machine': <OnThisMachine />, 'core:desktop-live': <DesktopLiveView />,
  }
  return <div className="grid grid-cols-1 gap-l lg:grid-cols-2" data-testid="core-composition">{[...tiles].sort((a, b) => a.order - b.order).filter(tile => tile.ref in widgets).map(tile => <section key={tile.ref} data-core-ref={tile.ref} data-core-size={tile.size} className={`min-w-0 ${tile.size === 'full' || tile.size === 'l' ? 'lg:col-span-2' : ''} ${tile.size === 's' ? 'max-w-sm' : tile.size === 'l' ? 'max-w-5xl' : ''}`}><h2>{tile.ref.slice(5).replaceAll('-', ' ')}</h2>{widgets[tile.ref as keyof typeof widgets]}</section>)}</div>
}
