
export interface RouteEntry {
  route: string
  label: string
  id?: string
  needsData?: boolean
}

export const ROUTES: RouteEntry[] = [
  { route: 'dashboard', label: 'Home' },
  { route: 'chat', label: 'Chat', needsData: true },
  { route: 'projects', label: 'Projects', needsData: true },
  { route: 'knowledge', label: 'Knowledge', needsData: true },
  { route: 'tasks', label: 'Tasks', needsData: true },
  { route: 'inbox', label: 'Inbox', needsData: true },
  { route: 'triggers', label: 'Triggers', needsData: true },
  { route: 'files', label: 'Files', needsData: true },
  { route: 'artifacts', label: 'Artifacts', needsData: true },
  { route: 'terminal', label: 'Terminal' },
  { route: 'agents', label: 'Agents', needsData: true },
  { route: 'tools', label: 'Tools', needsData: true },
  { route: 'skills', label: 'Skills', needsData: true },
  { route: 'learning', label: 'Learning', needsData: true },
  { route: 'workflows', label: 'Workflows', needsData: true },
  { route: 'prompts', label: 'Prompts', needsData: true },
  { route: 'apps', label: 'Store', needsData: true },
  { route: 'settings', label: 'Settings' },
]

export const SETTINGS_PANELS = [
  'account', 'design', 'chat', 'providers', 'models', 'search', 'prompts', 'memory', 'evals',
  'agent', 'voice', 'apps', 'inbox', 'documents', 'notifications', 'security', 'secrets', 'devices',
  'sender-trust', 'guardrails',
  'external-access', 'audit',
  'doctor', 'diagnostics', 'tool-output', 'feedback', 'usage', 'routing', 'legibility',
  'ambient', 'companion', 'sources', 'packs', 'archive', 'portability', 'durability', 'updates',
] as const

export const SETTINGS_ROUTES: RouteEntry[] = SETTINGS_PANELS.map((id) => ({
  route: `settings/${id}`,
  label: `Settings › ${id}`,
  needsData: true,
}))

export const VIEW_ROUTES: RouteEntry[] = [
  { route: 'knowledge?view=graph', id: 'knowledge-graph', label: 'Knowledge › Graph', needsData: true },
]

export const NON_NAV_ROUTES: RouteEntry[] = [
  { route: 'mission-control', label: 'Mission Control', needsData: true },
  { route: 'notifications', label: 'Notifications', needsData: true },
  { route: 'discover', label: 'Discover', needsData: true },
  { route: 'app/shell/not-a-real-app', id: 'app-host-not-installed', label: 'App host › not installed', needsData: true },
  { route: 'app/shell/e2e-ui-fixture', id: 'app-host-contributed-ui', label: 'App host › contributed UI', needsData: true },
]

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
