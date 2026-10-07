
export interface RouteEntry {
  route: string
  label: string
  id?: string
  needsData?: boolean
  readySelector?: string
}

export const ROUTES: RouteEntry[] = [
  { route: 'dashboard', label: 'Home' },
  { route: 'chat', label: 'Chat', needsData: true },
  { route: 'chat/new', id: 'chat-new', label: 'New conversation', needsData: true, readySelector: '[data-gideon-chat-page]' },
  { route: 'chat/history', id: 'chat-history', label: 'Conversations', needsData: true, readySelector: 'span[data-type="title-l"]:text-is("Chat history")' },
  { route: 'projects', label: 'Projects', needsData: true },
  { route: 'knowledge', label: 'Knowledge', needsData: true },
  { route: 'hypermid', label: 'Hypermid', needsData: true, readySelector: '[role="region"][aria-label="Hypermid administration"]' },
  { route: 'rooms', label: 'Rooms', needsData: true, readySelector: 'aside.rooms-list[aria-label="Rooms"]' },
  { route: 'tasks', label: 'Tasks', needsData: true },
  { route: 'inbox', label: 'Inbox', needsData: true },
  { route: 'triggers', label: 'Triggers', needsData: true },
  { route: 'files', label: 'Files', needsData: true },
  { route: 'artifacts', label: 'Artifacts', needsData: true },
  { route: 'terminal', label: 'Terminal' },
  { route: 'capabilities/communications?view=calendar', id: 'capabilities-calendar', label: 'Calendar', needsData: true, readySelector: 'section[aria-label="Calendar daily review"]' },
  { route: 'capabilities/workspace', id: 'capabilities-workspace', label: 'Workspaces', needsData: true, readySelector: 'h2:text-is("Workspace contexts")' },
  { route: 'capabilities/knowledge', id: 'capabilities-knowledge', label: 'Reading & capture', needsData: true, readySelector: 'h1:text-is("On this day")' },
  { route: 'capabilities/identity', id: 'capabilities-identity', label: 'Identity & goals', needsData: true, readySelector: 'h1:text-is("Your life stories")' },
  { route: 'capabilities/wellbeing', id: 'capabilities-wellbeing', label: 'Health', needsData: true, readySelector: 'h1:text-is("Measurements")' },
  { route: 'capabilities/communications', id: 'capabilities-communications', label: 'People & calendar', needsData: true, readySelector: 'h1:text-is("People")' },
  { route: 'capabilities/media', id: 'capabilities-media', label: 'Images & video', needsData: true, readySelector: 'section[aria-label="Image sketches"]' },
  { route: 'capabilities/creative', id: 'capabilities-creative', label: 'Writing', needsData: true, readySelector: 'h1:text-is("Creative ingredients")' },
  { route: 'capabilities/music', id: 'capabilities-music', label: 'Music & 3D', needsData: true, readySelector: 'h1:text-is("Repertoire and practice")' },
  { route: 'capabilities/experience', id: 'capabilities-experience', label: 'Stories & worlds', needsData: true, readySelector: 'main[aria-label="Interactive stories"]' },
  { route: 'capabilities/platform', id: 'capabilities-platform', label: 'Models & services', needsData: true, readySelector: 'h1:text-is("Providers")' },
  { route: 'agents', label: 'Agents', needsData: true },
  { route: 'tools', label: 'Tools', needsData: true },
  { route: 'skills', label: 'Skills', needsData: true },
  { route: 'learning', label: 'Learning', needsData: true },
  { route: 'workflows', label: 'Workflows', needsData: true },
  { route: 'prompts', label: 'Prompts', needsData: true },
  { route: 'apps', label: 'Store', needsData: true },
  { route: 'apps/manage', id: 'apps-manage', label: 'Manage apps', needsData: true, readySelector: 'h1:text-is("Apps")' },
  { route: 'experiments', label: 'Experiments', needsData: true, readySelector: 'h1:text-is("Experiments")' },
  { route: 'settings', label: 'Settings' },
]

export const SETTINGS_PANELS = [
  'account', 'design', 'chat', 'providers', 'models', 'search', 'prompts', 'memory', 'evals',
  'agent', 'voice', 'apps', 'inbox', 'documents', 'notifications', 'security', 'secrets', 'devices',
  'sender-trust', 'guardrails',
  'external-access', 'audit',
  'doctor', 'diagnostics', 'tool-output', 'feedback', 'usage', 'routing', 'legibility',
  'ambient', 'companion', 'sources', 'packs', 'archive', 'portability', 'durability', 'updates', 'runtime-config',
] as const

export const SETTINGS_ROUTES: RouteEntry[] = SETTINGS_PANELS.map((id) => ({
  route: `settings/${id}`,
  label: `Settings › ${id}`,
  needsData: true,
}))

export const VIEW_ROUTES: RouteEntry[] = [
  { route: 'knowledge?view=graph', id: 'knowledge-graph', label: 'Knowledge › Graph', needsData: true },
  { route: 'tasks?view=dag', id: 'tasks-dag', label: 'Tasks › Dependency graph', needsData: true },
]

export const NON_NAV_ROUTES: RouteEntry[] = [
  { route: 'capabilities', label: 'Capabilities', readySelector: 'h1:text-is("What would you like to do?")' },
  { route: 'mission-control', label: 'Mission Control', needsData: true },
  { route: 'notifications', label: 'Notifications', needsData: true },
  { route: 'discover', label: 'Discover', needsData: true },
  { route: 'app/shell/not-a-real-app', id: 'app-host-not-installed', label: 'App host › not installed', needsData: true },
  { route: 'app/shell/e2e-ui-fixture', id: 'app-host-contributed-ui', label: 'App host › contributed UI', needsData: true },
]

export const THEMES = ['light', 'dark'] as const
export type Theme = (typeof THEMES)[number]
