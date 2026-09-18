
export interface NamespacePolicy {
  staleAfterMs: number
  why: string
}

const SECOND = 1000
const MINUTE = 60 * SECOND

const LIVE: NamespacePolicy = { staleAfterMs: 5 * SECOND, why: 'live — a few seconds old is already suspect' }
const COLLECTION: NamespacePolicy = { staleAfterMs: 30 * SECOND, why: 'user-owned collection — busted on write, timer is the out-of-band backstop' }
const CONFIG: NamespacePolicy = { staleAfterMs: 2 * MINUTE, why: 'config/schema — slow to fetch, rarely changes out of band' }

export const CACHE_NAMESPACES: Record<string, NamespacePolicy> = {
  agent: LIVE,
  agents: LIVE,
  'app-catalog': CONFIG,
  'app-config': CONFIG,
  'app-host': CONFIG,
  'app-uninstall': COLLECTION,
  apps: COLLECTION,
  artifacts: COLLECTION,
  autonomy: CONFIG,
  browse: LIVE,
  chat: COLLECTION,
  code: COLLECTION,
  companion: CONFIG,
  'computer-use': LIVE,
  config: CONFIG,
  dashboard: LIVE,
  dirtree: COLLECTION,
  discover: COLLECTION,
  inbox: LIVE,
  'inbox-companion': LIVE,
  knowledge: COLLECTION,
  learning: COLLECTION,
  loop: COLLECTION,
  loops: LIVE,
  'loops-companion': LIVE,
  models: CONFIG,
  notifications: LIVE,
  'notifications-companion': LIVE,
  onboarding: CONFIG,
  proactive: LIVE,
  prompt: COLLECTION,
  'prompt-snippets': COLLECTION,
  prompts: COLLECTION,
  projects: COLLECTION,
  settings: CONFIG,
  skill: COLLECTION,
  'skill-proposals': COLLECTION,
  'skill-proposals-count': COLLECTION,
  skills: COLLECTION,
  snippet: COLLECTION,
  system: LIVE,
  tasklist: COLLECTION,
  tasks: COLLECTION,
  'tasks-all': COLLECTION,
  'tasks-companion': COLLECTION,
  tools: CONFIG,
  triggers: COLLECTION,
  workflows: LIVE,
}

export const UNDECLARED_POLICY: NamespacePolicy = {
  staleAfterMs: 0,
  why: 'undeclared namespace — treated as always stale so nothing silently presents unknown-age data as fresh',
}

export function namespaceOf(key: string): string {
  const i = key.indexOf(':')
  return i === -1 ? key : key.slice(0, i)
}

export function policyFor(key: string): NamespacePolicy {
  return CACHE_NAMESPACES[namespaceOf(key)] ?? UNDECLARED_POLICY
}

export function staleAfterMsFor(key: string): number {
  return policyFor(key).staleAfterMs
}
