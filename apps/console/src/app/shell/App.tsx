import { capabilityAreas, capabilityNavigationId } from '../../features/capabilities/navigation'
import VoiceControls from './VoiceControls'
import './shell.css'
import { Suspense, useEffect, useRef, useState, type ComponentType } from 'react'
import { useApplicationEvents, useShellNavigation, useTerminalShell } from './shellControllers'
import { MotionConfig, motion } from 'framer-motion'
import { ease, duration, useReducedMotion } from '../../shared/theme/motion'
import { armCueAudio } from '../../shared/theme/soundCues'
import { installPushCuePlayback } from './pushCuePlayback'
import { Bell, Blocks, BookOpen, Brain, Compass, FileCode, FileText, Files, FlaskConical, FolderKanban, Inbox, LayoutDashboard, ListChecks, Loader2, MessageSquare, Radar, Settings, Sparkles, Terminal, Users, Workflow, Wrench, Zap } from 'lucide-react'
import { NavRail, type NavItem } from '../../shared/ui/NavRail'
import { ShellCornerLeft, ShellCornerRight } from '../../shared/ui/ShellCorners'
import { IncidentBanner } from './IncidentBanner'
import { ChatPage } from '../../features/ChatPage'
import { useIdentity } from './identity'
import { Onboarding } from './Onboarding'
import { peekOnboardingExit, clearOnboardingExit, setOnboardingExit } from '../../features/onboarding/exitTo'
import { ProductTour } from '../../features/onboarding/ProductTour'
import { parseRouteHash, useHashRoute } from './useHashRoute'
import { installRoutePreload, lazyRoute } from './routePreload'
import { useIsMobile } from './useIsMobile'
import type { RouteProps } from './useQueryState'
import { ErrorBoundary } from './ErrorBoundary'
import { syncAppGenUiComponents } from './appGenUiLayer'
import { api } from '../../shared/data/api'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { ACTIVE_LOOP_STATUSES } from '../../shared/data/loopStatus'
import { CommandPalette, type Command } from './CommandPalette'
import { TerminalDrawer } from '../../features/terminal/TerminalDrawer'
import { Toaster } from '../../shared/ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import { McpElicitationCards } from './McpElicitation'
import { useNativeNotifications } from '../../shared/data/nativeNotifications'
import { DialogHost } from '../../shared/ui/dialog/DialogHost'
import { PersonalityShellElement } from './personality'
import { UpdateProgressOverlay } from '../../shared/ui/UpdateProgressOverlay'
import { LoadingStatus } from '../../shared/ui/ListScaffold'
import { useQuery } from '../../shared/data/data'
import { resolveAppIcon } from '../../features/apps/appIcon'
import { useWidgetActionLauncher } from '../../shared/ui/widget/useWidgetActionBridge'
import { getNavApps, onNavAppsChange } from '../../features/apps/navApps'
import { isDisclosed, undisclosedCount, useNavDisclosure } from './navDisclosure'
import type { AppSummary } from '../../shared/data/api'
import { onTaskListCreated } from '../../shared/data/taskListCount'
import { useNotificationToasts } from './useNotificationToasts'

const LoopsSection = lazyRoute('loops', () => import('../../features/loops/LoopsSection').then((m) => ({ default: m.LoopsSection })))
const CodeSection = lazyRoute('code', () => import('../../features/code/CodeSection').then((m) => ({ default: m.CodeSection })))
const SettingsPage = lazyRoute('settings', () => import('../../features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const AgentsSection = lazyRoute('agents', () => import('../../features/agents/AgentsSection').then((m) => ({ default: m.AgentsSection })))
const CapabilitiesSection = lazyRoute('capabilities', () => import('../../features/capabilities/CapabilitiesSection'))
const RoomsSection = lazyRoute('rooms', () => import('../../features/rooms/RoomsSection').then((m) => ({ default: m.RoomsSection })))
const NotificationsPage = lazyRoute('notifications', () => import('../../features/notifications/NotificationsPage').then((m) => ({ default: m.NotificationsPage })))
const TriggersSection = lazyRoute('triggers', () => import('../../features/triggers/TriggersSection').then((m) => ({ default: m.TriggersSection })))
const LearningPage = lazyRoute('learning', () => import('../../features/learning/LearningPage').then((m) => ({ default: m.LearningPage })))
const TasksSection = lazyRoute('tasks', () => import('../../features/tasks/TasksSection').then((m) => ({ default: m.TasksSection })))
const ProjectsSection = lazyRoute('projects', () => import('../../features/projects/ProjectsSection').then((m) => ({ default: m.ProjectsSection })))
const PromptsSection = lazyRoute('prompts', () => import('../../features/prompts/PromptsSection').then((m) => ({ default: m.PromptsSection })))
const WorkflowsSection = lazyRoute('workflows', () => import('../../features/workflows/WorkflowsSection').then((m) => ({ default: m.WorkflowsSection })))
const ExperimentsPage = lazyRoute('experiments', () => import('../../features/experiments/ExperimentsPage').then((m) => ({ default: m.ExperimentsPage })))
const SkillsPage = lazyRoute('skills', () => import('../../features/skills/SkillsPage').then((m) => ({ default: m.SkillsPage })))
const ToolsPage = lazyRoute('tools', () => import('../../features/tools/ToolsPage').then((m) => ({ default: m.ToolsPage })))
const KnowledgeSection = lazyRoute('knowledge', () => import('../../features/knowledge/KnowledgeSection').then((m) => ({ default: m.KnowledgeSection })))
const KnowledgeReadingPage = lazyRoute('knowledge', () => import('../../features/knowledge/KnowledgeReadingPage').then((m) => ({ default: m.KnowledgeReadingPage })))
const LoopSection = lazyRoute('loop', () => import('../../features/loop/LoopSection').then((m) => ({ default: m.LoopSection })))
const InboxPage = lazyRoute('inbox', () => import('../../features/inbox/InboxPage').then((m) => ({ default: m.InboxPage })))
const FilesSection = lazyRoute('files', () => import('../../features/files/FilesSection').then((m) => ({ default: m.FilesSection })))
const ArtifactsSection = lazyRoute('artifacts', () => import('../../features/artifacts/ArtifactsSection').then((m) => ({ default: m.ArtifactsSection })))
const AppsSection = lazyRoute('apps', () => import('../../features/apps/AppsSection').then((m) => ({ default: m.AppsSection })))
const AppHostPage = lazyRoute('app', () => import('../../features/apps/AppHostPage').then((m) => ({ default: m.AppHostPage })))
const TerminalPage = lazyRoute('terminal', () => import('../../features/terminal/TerminalPage').then((m) => ({ default: m.TerminalPage })))
const DashboardPage = lazyRoute('dashboard', () => import('../../features/dashboard/DashboardPage').then((m) => ({ default: m.DashboardPage })))
const MissionControl = lazyRoute('mission-control', () => import('../../features/dashboard/MissionControl').then((m) => ({ default: m.MissionControl })))
const DiscoverPage = lazyRoute('discover', () => import('../../features/discover/DiscoverPage').then((m) => ({ default: m.DiscoverPage })))
const CompanionPage = lazyRoute('companion', () => import('../../features/companion/CompanionPage').then((m) => ({ default: m.CompanionPage })))

installRoutePreload()

const NAV: NavItem[] = [
  { id: 'dashboard', label: 'Home', icon: LayoutDashboard },
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'rooms', label: 'Rooms', icon: Users },
  { id: 'projects', label: 'Projects', icon: FolderKanban },
  { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { id: 'tasks', label: 'Tasks', icon: ListChecks, section: 'Platform' },
  { id: 'inbox', label: 'Inbox', icon: Inbox, section: 'Platform' },
  { id: 'triggers', label: 'Triggers', icon: Zap, section: 'Platform' },
  { id: 'files', label: 'Files', icon: Files, section: 'Platform' },
  { id: 'artifacts', label: 'Artifacts', icon: FileCode, section: 'Platform' },
  { id: 'terminal', label: 'Terminal', icon: Terminal, section: 'Platform' },
  ...capabilityAreas.map(area => ({ id: `capabilities/${area.id}`, label: area.label, section: area.group, icon: area.icon })),
  { id: 'agents', label: 'Agents', icon: Users, section: 'Capabilities' },
  { id: 'tools', label: 'Tools', icon: Wrench, section: 'Capabilities' },
  { id: 'skills', label: 'Skills', icon: Sparkles, section: 'Capabilities' },
  { id: 'learning', label: 'Learning', icon: Brain, section: 'Capabilities' },
  { id: 'prompts', label: 'Prompts', icon: FileText, section: 'Capabilities' },
  { id: 'workflows', label: 'Workflows', icon: Workflow, section: 'Capabilities' },
  { id: 'experiments', label: 'Experiments', icon: FlaskConical, section: 'Capabilities' },
  { id: 'apps', label: 'Apps', icon: Blocks, section: 'Apps' },
  { id: 'settings', label: 'Settings', icon: Settings, pinBottom: true },
]
const ROUTABLE = new Set([...NAV.map((n) => n.id), 'notifications', 'discover', 'loop', 'loops', 'code', 'app', 'mission-control'])

function PageFallback() {
  return (
    <div role="status" aria-busy="true" data-visual-state="waiting" className="flex h-full items-center justify-center">
      <LoadingStatus />
      <Loader2 size={22} className="animate-spin text-on-surface-low" />
    </div>
  )
}

const pageComponents: Record<string, ComponentType<RouteProps>> = {
  dashboard: DashboardPage,
  'mission-control': MissionControl,
  chat: ChatPage,
  capabilities: CapabilitiesSection,
  rooms: RoomsSection,
  loop: LoopSection,
  loops: LoopsSection,
  code: CodeSection,
  notifications: NotificationsPage,
  discover: DiscoverPage,
  triggers: TriggersSection,
  tasks: TasksSection,
  projects: ProjectsSection,
  knowledge: KnowledgeSection,
  inbox: InboxPage,
  files: FilesSection,
  artifacts: ArtifactsSection,
  terminal: TerminalPage,
  prompts: PromptsSection,
  workflows: WorkflowsSection,
  experiments: ExperimentsPage,
  skills: SkillsPage,
  learning: LearningPage,
  tools: ToolsPage,
  agents: AgentsSection,
  apps: AppsSection,
  app: AppHostPage,
  settings: SettingsPage,
}

function renderPage(route: string, props: RouteProps) {
  if (route === 'knowledge') {
    const parts = (props.sub || '').split('/')
    const readingId = parts[0] === 'read' && parts[1]
      ? parts.slice(1).join('/')
      : parts[0] === 'item' && parts[1] && props.query.read === '1'
        ? parts.slice(1).join('/')
        : ''
    if (readingId) {
      const back = parts[0] === 'item' ? `knowledge/item/${encodeURIComponent(readingId)}` : 'knowledge'
      return <KnowledgeReadingPage key={readingId} id={readingId} onBack={() => props.navigate(back)} />
    }
  }
  const Page = pageComponents[route]
  return Page ? <Page {...props} /> : <div className="flex h-full items-center justify-center text-on-surface-low" data-type="headline-s">{NAV.find((item) => item.id === route)?.label} — coming soon</div>
}

export function App() {
  const reducedMotion = useReducedMotion()
  useEffect(() => {
    document.documentElement.toggleAttribute('data-reduced-motion', reducedMotion)
    return () => document.documentElement.removeAttribute('data-reduced-motion')
  }, [reducedMotion])
  return (
    <MotionConfig reducedMotion={reducedMotion ? 'always' : 'never'}>
      <AppInner />
    </MotionConfig>
  )
}

function AppInner() {
  const { route, sub, navEpoch, navigate, query, setQuery } = useHashRoute('dashboard')
  const activeChatSession = route === 'chat' && sub && sub !== 'new' && sub !== 'history' ? sub : ''
  useApprovalToasts(activeChatSession)
  useNativeNotifications(navigate)
  useNotificationToasts()
  useEffect(() => { armCueAudio() }, [])
  useEffect(() => installPushCuePlayback(), [])
  const { onboarded, loaded } = useIdentity()
  const isMobile = useIsMobile()
  const rail = useShellNavigation(isMobile, navigate)
  const railCollapsed = rail.collapsed
  const mobileNavOpen = rail.open
  const toggleNav = rail.toggle
  const onNavSelect = rail.select
  const [voiceOpen, setVoiceOpen] = useState(false)
  const [activeLoops, setActiveLoops] = useState(0)
  const [taskListCount, setTaskListCount] = useState(0)
  useEffect(() => {
    let active = true
    api.taskLists().then((lists) => { if (active) setTaskListCount(lists.length) }).catch(() => {})
    const unsubscribe = onTaskListCreated(() => setTaskListCount((count) => count + 1))
    return () => { active = false; unsubscribe() }
  }, [])
  useVisiblePoll(() => {
    api.uLoops().then((ls) => setActiveLoops(ls.filter((l) => ACTIVE_LOOP_STATUSES.has(l.status)).length)).catch(() => {})
  }, 8000)

  const { data: installedApps } = useQuery<AppSummary[]>(
    'apps', () => api.apps(), { persist: true },
  )
  useEffect(() => {
    if (!installedApps) return
    void syncAppGenUiComponents(installedApps)
  }, [installedApps])
  const [navAppSet, setNavAppSet] = useState<string[]>(() => getNavApps())
  useEffect(() => onNavAppsChange(() => setNavAppSet(getNavApps())), [])
  const appNavItems: NavItem[] = (installedApps ?? [])
    .filter((a) => a.enabled && a.hasUI && (a.uiPages?.length ?? 0) > 0 && navAppSet.includes(a.name))
    .map((a) => ({
      id: `app/${a.name}`,
      label: a.uiPages[0].label || a.displayName,
      icon: resolveAppIcon(a.uiPages[0].icon || a.icon),
      section: 'Apps',
    }))

  const appBadges = useApplicationEvents(navigate)
  useWidgetActionLauncher()
  const terminal = useTerminalShell()
  const termDrawer = terminal.open

  useEffect(() => {
    if (!loaded) return
    if (!onboarded && route !== 'onboarding') {
      if (ROUTABLE.has(route)) setOnboardingExit(location.hash)
      navigate('onboarding?step=name', { replace: true })
    }
    else if (onboarded && route === 'onboarding') {
      const destination = peekOnboardingExit()
      navigate(ROUTABLE.has(parseRouteHash(destination, 'dashboard').route) ? destination : 'dashboard', { replace: true })
    }
    else if (onboarded && route !== 'companion' && !ROUTABLE.has(route)) navigate('dashboard', { replace: true })
    else if (onboarded) clearOnboardingExit()
  }, [loaded, onboarded, route, navigate])

  const { mode: navMode, pinned: navPinned, setMode: setNavMode, pin: pinNav } = useNavDisclosure()

  const rendered = ROUTABLE.has(route) ? route : 'dashboard'
  const active = (rendered === 'loop' || rendered === 'loops' || rendered === 'code') ? 'projects'
    : rendered === 'app' ? `app/${(sub ?? '').split('/')[0]}`
      : rendered === 'capabilities' ? capabilityNavigationId(sub ?? '') : rendered

  useEffect(() => {
    if (navMode !== 'starter') return
    if (isDisclosed(active, navMode, navPinned)) return
    pinNav(active)
  }, [active, navMode, navPinned, pinNav])

  const embedRef = useRef(query.embed === '1')
  if (query.embed === '1') embedRef.current = true

  if (!loaded) return <div data-visual-state="waiting" className="grid h-full place-items-center" style={{ background: 'var(--color-canvas)' }}><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
  if (route === 'onboarding' || !onboarded) return <Onboarding query={query} setQuery={setQuery} />

  if (route === 'companion') {
    return (
      <>
        <ErrorBoundary resetKey="companion">
          <Suspense fallback={<PageFallback />}>
            <CompanionPage sub={sub} navigate={navigate} navEpoch={navEpoch} query={query} setQuery={setQuery} />
          </Suspense>
        </ErrorBoundary>
        {
}
        <McpElicitationCards />
        <Toaster />
      </>
    )
  }

  if (embedRef.current) {
    const embedRoute = ROUTABLE.has(route) ? route : 'chat'
    const embedNavigate: typeof navigate = (path, opts) => {
      const [p, q = ''] = path.replace(/^#?\/?/, '').split('?')
      const usp = new URLSearchParams(q)
      usp.set('embed', '1')
      navigate(`${p}?${usp.toString()}`, opts)
    }
    return (
      <div className="h-full" style={{
        background: 'var(--color-canvas)',
        '--shell-corner-l': '0px',
        '--shell-corner-r': '0px',
        '--shell-corner-rh': '0px',
      } as React.CSSProperties}>
        <ErrorBoundary resetKey={embedRoute}>
          <Suspense fallback={<PageFallback />}>
            {renderPage(embedRoute, { sub, navigate: embedNavigate, navEpoch, query, setQuery })}
          </Suspense>
        </ErrorBoundary>
        {
}
        <McpElicitationCards />
        <Toaster />
        <DialogHost />
      </div>
    )
  }

  const updatesCount = (installedApps ?? []).filter((a) => a.updateAvailable).length
  const appBadgeTotal = Object.values(appBadges).reduce((a, b) => a + b, 0) + updatesCount
  const navItems: NavItem[] = []
  for (const n of NAV) {
    if (n.id === 'projects' && activeLoops > 0) {
      navItems.push({ ...n, badge: String(activeLoops), badgeLabel: `${activeLoops} active loop${activeLoops === 1 ? '' : 's'}` })
    } else if (n.id === 'tasks' && taskListCount > 0) {
      navItems.push({ ...n, badge: String(taskListCount), badgeLabel: `${taskListCount} task list${taskListCount === 1 ? '' : 's'}` })
    } else if (n.id === 'apps' && appBadgeTotal > 0) {
      navItems.push({
        ...n,
        badge: String(appBadgeTotal),
        badgeLabel: appBadgeTotal === updatesCount
          ? `${updatesCount} app update${updatesCount === 1 ? '' : 's'} available`
          : undefined,
      })
    } else navItems.push(n)
    if (n.id === 'apps') {
      for (const ai of appNavItems) {
        const badge = appBadges[ai.id.slice('app/'.length)]
        navItems.push(badge ? { ...ai, badge: String(badge) } : ai)
      }
    }
  }
  const disclosedItems = navItems.filter((n) => isDisclosed(n.id, navMode, navPinned))
  const moreCount = undisclosedCount(navItems.map((n) => n.id), navPinned)

  const commands: Command[] = [
    { id: 'go:capabilities', label: 'All workspaces', hint: 'Go to', icon: Sparkles, run: () => navigate('capabilities') },
    ...NAV.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: n.section ?? '', run: () => navigate(n.id) })),
    ...appNavItems.map((n) => ({ id: `go:${n.id}`, label: n.label, hint: 'Go to', icon: n.icon, keywords: 'app', run: () => navigate(n.id) })),
    { id: 'go:mission-control', label: 'Mission Control', hint: 'Go to', icon: Radar, keywords: 'attention lanes approvals needs approval your turn working idle', run: () => navigate('mission-control') },
    { id: 'go:notifications', label: 'Notifications', hint: 'Go to', icon: Bell, keywords: 'alerts feed', run: () => navigate('notifications') },
    { id: 'go:discover', label: 'Discover', hint: 'Go to', icon: Compass, keywords: 'tips tour learn features guide', run: () => navigate('discover') },
    { id: 'act:terminal-drawer', label: 'Toggle terminal drawer', hint: 'Action · ⌘`', icon: Terminal, keywords: 'shell pty console', run: () => terminal.toggle() },
    { id: 'act:voice', label: 'Voice controls', hint: 'Action', icon: Sparkles, run: () => setVoiceOpen(true) },
    { id: 'act:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: () => navigate('settings') },
  ]
  return (
    <div className="gideon-shell flex h-full" style={{ background: 'var(--color-canvas)' }}>
      <NavRail items={disclosedItems} activeId={active} onSelect={onNavSelect} collapsed={railCollapsed}
        overlay={isMobile} overlayOpen={isMobile && mobileNavOpen} onScrimClick={() => rail.close()}
        disclosure={{
          expanded: navMode === 'expert',
          moreCount,
          onToggle: () => setNavMode(navMode === 'expert' ? 'starter' : 'expert'),
        }} />
      <main className="gideon-workspace relative flex-1 min-w-0">
        { }
        <ShellCornerLeft collapsed={railCollapsed} onToggle={toggleNav} />
        <ShellCornerRight terminalOpen={termDrawer} onToggleTerminal={() => terminal.toggle()} navigate={navigate} />
        {
}
        <IncidentBanner />
        <ErrorBoundary resetKey={rendered}>
          <Suspense fallback={<PageFallback />}>
            {
}
            <motion.div
              key={rendered}
              className="h-full"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: duration.medium, ease: ease.emphasizedDecel }}
            >
              {renderPage(rendered, { sub, navigate, navEpoch, query, setQuery })}
            </motion.div>
          </Suspense>
        </ErrorBoundary>
      </main>
      <VoiceControls open={voiceOpen} onClose={() => setVoiceOpen(false)} items={[...navItems, { id: 'capabilities', label: 'Capabilities', icon: Sparkles }]} navigate={navigate} currentRoute={[route, sub].filter(Boolean).join('/')} />
      <CommandPalette commands={commands} />
      {
}
      <ProductTour route={rendered} navigate={navigate} />
      {
}
      <PersonalityShellElement />
      <McpElicitationCards />
      <Toaster />
      <DialogHost />
      {
}
      <UpdateProgressOverlay />
      <TerminalDrawer open={termDrawer} onClose={() => terminal.close()} onOpenFull={() => { terminal.close(); navigate('terminal') }} />
    </div>
  )
}
