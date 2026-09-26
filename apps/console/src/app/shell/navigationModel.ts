import { Blocks, BookOpen, Brain, CalendarDays, FileCode, FileText, Files, FlaskConical, FolderKanban, Inbox, LayoutDashboard, Link2, ListChecks, MessageSquare, MessagesSquare, PenLine, Settings, Sparkles, Terminal, Users, Workflow, Wrench, Zap } from 'lucide-react'
import type { NavItem } from '../../shared/ui/NavRail'
import { capabilityAreas, capabilityNavigationId } from '../../features/capabilities/navigation'
import type { ChatSessionSummary } from '../../shared/data/api'
import { sessionTitle } from '../../shared/data/sessionTitle'
import { sessionRecencyMs } from '../../shared/data/epoch'

export const FAMILIAR_IDS = [
  'chat/new', 'chat/history', 'projects', 'files',
  'capabilities/communications?view=calendar', 'inbox', 'apps', 'settings',
] as const

export function navigationItems(hosted = false, translate: (value: string) => string = value => value): NavItem[] {
  const section = (value: string) => translate(value)
  const items: NavItem[] = [
    { id: 'chat/new', label: translate('New conversation'), icon: PenLine, section: section('Your space') },
    { id: 'chat/history', label: translate('Conversations'), icon: MessagesSquare, section: section('Your space') },
    { id: 'projects', label: translate('Projects'), icon: FolderKanban, section: section('Your space') },
    { id: 'files', label: translate('Files'), icon: Files, section: section('Your space') },
    { id: 'capabilities/communications?view=calendar', label: translate('Calendar'), icon: CalendarDays, section: section('Your space') },
    { id: 'inbox', label: translate('Notifications'), icon: Inbox, section: section('Your space') },
    { id: 'apps', label: translate('Apps'), icon: Blocks, section: section('Your apps') },
    { id: 'apps/manage', label: translate('Manage apps'), icon: Blocks, section: section('Your apps') },
    { id: 'dashboard', label: translate('Home'), icon: LayoutDashboard, section: section('More') },
    { id: 'chat', label: translate('Chat'), icon: MessageSquare, section: section('More') },
    { id: 'rooms', label: translate('Rooms'), icon: Users, section: section('More') },
    { id: 'knowledge', label: translate('Knowledge'), icon: BookOpen, section: section('More') },
    { id: 'tasks', label: translate('Tasks'), icon: ListChecks, section: section('More') },
    { id: 'triggers', label: translate(hosted ? 'Automations' : 'Triggers'), icon: Zap, section: section('More') },
    { id: 'artifacts', label: translate('Artifacts'), icon: FileCode, section: section('More') },
    { id: 'terminal', label: translate('Terminal'), icon: Terminal, section: section('More') },
    ...capabilityAreas.map(area => ({ id: `capabilities/${area.id}`, label: translate(area.label), section: section(area.group), icon: area.icon })),
    { id: 'agents', label: translate('Agents'), icon: Users, section: section('More') },
    { id: 'tools', label: translate('Tools'), icon: Wrench, section: section('More') },
    { id: 'skills', label: translate('Skills'), icon: Sparkles, section: section('More') },
    { id: 'learning', label: translate('Learning'), icon: Brain, section: section('More') },
    { id: 'prompts', label: translate('Prompts'), icon: FileText, section: section('More') },
    { id: 'workflows', label: translate('Workflows'), icon: Workflow, section: section('More') },
    { id: 'experiments', label: translate('Experiments'), icon: FlaskConical, section: section('More') },
    { id: 'settings', label: translate('Your account'), icon: Settings, pinBottom: true },
  ]
  if (hosted) items.splice(items.findIndex(item => item.id === 'apps'), 0,
    { id: 'connections', label: translate('Connections'), icon: Link2, section: section('Your apps') })
  return items
}

export const ROUTABLE_ROOTS = new Set([
  'dashboard', 'chat', 'capabilities', 'rooms', 'loop', 'loops', 'code', 'notifications',
  'discover', 'triggers', 'tasks', 'projects', 'knowledge', 'inbox', 'files', 'artifacts',
  'terminal', 'prompts', 'workflows', 'experiments', 'skills', 'learning', 'tools',
  'agents', 'apps', 'app', 'settings', 'mission-control',
])

export function activeNavigationId(route: string, sub: string, query: Record<string, string>): string {
  if (route === 'chat') return sub === 'history' ? 'chat/history' : sub === 'new' || !sub ? 'chat/new' : `chat/${encodeURIComponent(sub)}`
  if (route === 'apps') return sub === 'manage' || !!query.view ? 'apps/manage' : sub ? `apps/${sub}` : 'apps'
  if (route === 'loop' || route === 'loops' || route === 'code') return 'projects'
  if (route === 'app') return `app/${sub.split('/')[0]}`
  if (route === 'capabilities') return sub.startsWith('communications') && query.view === 'calendar'
    ? 'capabilities/communications?view=calendar' : capabilityNavigationId(sub)
  return route
}

export function managesApps(sub: string, query: Record<string, string>): boolean {
  return sub !== '' || !!query.view
}

export function recentSessionItems(sessions: readonly ChatSessionSummary[] | undefined, translate: (value: string) => string = value => value): NavItem[] {
  return (sessions ?? []).filter(session => (session.origin ?? 'manual') === 'manual' && session.lifecycle !== 'archived')
    .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a)).slice(0, 3)
    .map(session => ({ id: `chat/${encodeURIComponent(session.key)}`, label: sessionTitle(session), icon: MessageSquare, section: translate('Recent') }))
}
