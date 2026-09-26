import { useComposition, CompositionEditor } from '../capabilities/platform/Composition'
import { CoreWidgets } from '../capabilities/platform/CoreWidgets'
import { useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import {
  MessageSquare, History, type LucideIcon,
  MessageSquarePlus, ListTodo, BookOpen, FolderKanban, FileCode2, TerminalSquare, Sparkles, Compass,
  Package, HardDrive, Orbit, Monitor,
} from 'lucide-react'
import { DashboardLiveProvider } from './DashboardLive'
import { PinnedTiles } from './PinnedTiles'
import { SurfaceOverlay } from '../../shared/ui/surfaces/SurfaceOverlay'
import { AgentWorld } from './world/AgentWorld'
import { HeroPulse } from './widgets/HeroPulse'
import { ActionCenter } from './widgets/ActionCenter'
import { ActiveWork } from './widgets/ActiveWork'
import { TasksWidget } from './widgets/TasksWidget'
import { Suggestions } from './widgets/Suggestions'
import { Discover } from './widgets/Discover'
import { PinnedArtifacts } from './widgets/PinnedArtifacts'
import { OnThisMachine } from './widgets/OnThisMachine'
import { DesktopLiveView } from './widgets/DesktopLiveView'
import { ScheduleWidget } from './widgets/ScheduleWidget'
import { SystemHealth } from './widgets/SystemHealth'
import { TopBar } from '../../shared/ui/TopBar'
import { useIdentity, firstNameOf } from '../../app/shell/identity'
import { api, type ChatSessionSummary } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { sessionRecencyMs } from '../../shared/data/epoch'
import { sessionTitle } from '../../shared/data/sessionTitle'
import { spring, expr } from '../../shared/theme/motion'
import { EntranceGroup, EntranceRegion } from '../../shared/ui/motion'
import { ComposerStage } from '../../shared/ui/ComposerStage'
import { useComposerData } from '../../shared/data/useComposerData'
import type { ComposerValue } from '../../shared/ui/composer/types'
import type { RouteProps } from '../../app/shell/useQueryState'
import { InlineError } from '../../shared/ui/InlineError'

export function DashboardPage(route: RouteProps) {
  const { name } = useIdentity()
  const composition = useComposition()
  const custom = composition.selected && !composition.selected.preset
  return (
    <DashboardLiveProvider>
      <div className="flex h-full flex-col overflow-hidden">
        {
}
        <TopBar
          contentAligned
          left={(
            <h1 data-type="headline-s" className="min-w-0 truncate text-on-surface">{greetingFor(name)}</h1>
          )}
          right={(
            <div className="hidden lg:block">
              <HeroPulse variant="header" {...route} />
            </div>
          )}
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {
}
          <EntranceGroup className="mx-auto flex w-full flex-col gap-2xl px-l py-xl" style={{ maxWidth: 'var(--content-width)' }}>
            <CompositionEditor model={composition} />
            <EntranceRegion><Launcher {...route} /></EntranceRegion>

            {
}
            <EntranceRegion className="lg:hidden" style={{ minHeight: 'calc(36px * var(--space-scale))' }}><HeroPulse {...route} /></EntranceRegion>

            {
}
            <PinnedTiles viewId={composition.selected?.id || 'overview'} />

            {
}
            <SurfaceOverlay surface="dashboard" />

            {
}
            {custom ? <CoreWidgets tiles={composition.selected!.tiles} route={route} /> : <>
            <EntranceRegion className="grid grid-cols-1 gap-2xl lg:grid-cols-2">
              <Section label="Needs you" icon={ListTodo} tour="approvals">
                <ActionCenter {...route} />
              </Section>
              <Section label="Active work" icon={Sparkles}>
                <ActiveWork {...route} />
              </Section>
            </EntranceRegion>

            <EntranceRegion className="grid grid-cols-1 gap-2xl lg:grid-cols-2">
              <Section label="Tasks" icon={ListTodo}>
                <TasksWidget {...route} />
              </Section>
              <Section label="Suggestions" icon={Sparkles}>
                <Suggestions {...route} />
              </Section>
            </EntranceRegion>

            {
}

            {
}
            <EntranceRegion className="min-w-0">
              <Section label="Agent world" icon={Orbit}>
                <AgentWorld />
              </Section>
            </EntranceRegion>

            {
}
            <EntranceRegion className="min-w-0">
              <Section label="Discover" icon={Compass}>
                <Discover {...route} />
              </Section>
            </EntranceRegion>

            {
}
            <EntranceRegion className="min-w-0">
              <Section label="Pinned artifacts" icon={Package}>
                <PinnedArtifacts {...route} />
              </Section>
            </EntranceRegion>

            {
}
            <EntranceRegion className="min-w-0">
              <Section label="On this machine" icon={HardDrive}>
                <OnThisMachine />
              </Section>
            </EntranceRegion>

            {
}
            <EntranceRegion className="min-w-0">
              <Section label="Desktop live view" icon={Monitor}>
                <DesktopLiveView />
              </Section>
            </EntranceRegion>

            <EntranceRegion className="min-w-0">
              <Section label="Recent activity" icon={History}>
                <ScheduleWidget {...route} />
              </Section>
            </EntranceRegion>

            {
}
            <EntranceRegion className="min-w-0 lg:hidden">
              <SystemRailIsland {...route} />
            </EntranceRegion>
            </>}
          </EntranceGroup>
        </div>
        {
}
        {!custom && <div className="hidden shrink-0 px-l pb-m pt-xs lg:block">
          <SystemRailIsland {...route} />
        </div>}
      </div>
    </DashboardLiveProvider>
  )
}

function SystemRailIsland(route: RouteProps) {
  return (
    <div
      className="@container mx-auto flex w-full items-center rounded-lg border border-outline-variant/50 bg-surface-low/70 px-l py-s shadow-rest backdrop-blur-md"
      style={{ maxWidth: 'var(--content-width)' }}
    >
      <SystemHealth {...route} />
    </div>
  )
}

function greetingFor(name: string | undefined): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return name ? `Good ${part}, ${firstNameOf(name)}` : `Good ${part}`
}


const JUMPS: { label: string; icon: LucideIcon; go: string }[] = [
  { label: 'Chat', icon: MessageSquarePlus, go: 'chat/new' },
  { label: 'Tasks', icon: ListTodo, go: 'tasks' },
  { label: 'Knowledge', icon: BookOpen, go: 'knowledge' },
  { label: 'Projects', icon: FolderKanban, go: 'projects' },
  { label: 'Files', icon: FileCode2, go: 'files' },
  { label: 'Terminal', icon: TerminalSquare, go: 'terminal' },
]

const LAUNCHER_CONTROLS = { agent: true, model: true, approval: false, reasoning: true, attach: true, mic: true, optimize: true, slash: true }

function Launcher({ navigate }: RouteProps) {
  const [text, setText] = useState('')
  const data = useComposerData()
  const [selection, setSelection] = useState<ComposerValue>({ agent: '', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
  const { data: sessions, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>(
    'chat:sessions:recent', () => api.chatSessions(), { persist: true },
  )

  const launch = () => {
    const t = text.trim()
    if (!t) { navigate('chat/new'); return }
    const params = new URLSearchParams({ seed: t })
    if (selection.agent) params.set('agent', selection.agent)
    if (selection.model && selection.model !== 'Auto') params.set('model', selection.model)
    navigate(`chat/new?${params}`)
    setText('')
  }

  const recent = (sessions ?? [])
    .filter((s) => s.title && !s.running)
    .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
    .slice(0, 4)

  return (
    <div className="flex flex-col gap-l">
      <ComposerStage value={text} onChange={setText} onSend={launch}
        placeholder="Ask anything, or start a task…"
        controls={LAUNCHER_CONTROLS} data={data}
        selection={selection} onSelect={(patch) => setSelection((s) => ({ ...s, ...patch }))}
        onAttach={() => {
          const t = text.trim()
          navigate(t ? `chat/new?seed=${encodeURIComponent(t)}` : 'chat/new')
        }}
      />

      {Boolean(sessionsError) && (
        <InlineError icon onRetry={refreshSessions}>{sessions === undefined ? 'Couldn’t load recent chats.' : 'Couldn’t refresh recent chats.'}</InlineError>
      )}

      <div className="flex flex-wrap items-center gap-xs">
        {JUMPS.map((j, i) => (
          <motion.button
            key={j.go}
            type="button"
            onClick={() => navigate(j.go)}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0, transition: { ...spring.spatialDefault, delay: Math.min(i * 0.03, 0.2) } }}
            whileHover={{ y: -expr(2, 0.3) }}
            className="inline-flex items-center gap-xs rounded-pill bg-surface-low px-m py-s text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface"
            data-type="label-m"
          >
            <j.icon size={15} className="text-primary" /> {j.label}
          </motion.button>
        ))}
      </div>

      {recent.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-l gap-y-xs">
          <span data-type="label-m" className="flex items-center gap-xs text-on-surface-low"><History size={12} /> Jump back in</span>
          {recent.map((s) => (
            <button
              key={s.key}
              type="button"
              onClick={() => navigate(`chat/${encodeURIComponent(s.key)}`)}
              className="inline-flex min-h-6 -my-0.5 items-center gap-xs text-on-surface-var transition-colors hover:text-on-surface"
              style={{ marginBlock: 'calc(-2px * var(--space-scale))' }}
              data-type="body-m"
            >
              <MessageSquare size={12} className="shrink-0 text-on-surface-low" /> <span className="max-w-[16rem] truncate">{sessionTitle(s)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Shared section frame for the dashboard's live widgets.
function Section({ label, icon: Icon, children, tour }: {
  label: string; icon: LucideIcon; children: ReactNode
  tour?: string
}) {
  return (
    <section data-tour={tour} className="gideon-dashboard-section flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <Icon size={14} className="shrink-0 text-on-surface-low" />
        {
}
        <h2 data-type="label-l" className="text-on-surface-var">{label}</h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {children}
    </section>
  )
}
