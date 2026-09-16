import { api } from '../../shared/data/api'

export type TryOneId = 'knowledge' | 'trigger' | 'loop'

export interface OutcomeFact { label: string; value: string }

export interface TryOneOutcome {

  headline: string
  facts: OutcomeFact[]

  href: string
  linkLabel: string
}

export const KNOWLEDGE_SEED = {
  title: 'How Gideon stores your data',
  content:
    'Everything Gideon knows lives in one home directory on this machine. Knowledge, memory, '
    + 'chat history, triggers and app state are all files under that home, so copying the home '
    + 'directory copies the whole agent, and deleting it deletes everything. Nothing is stored '
    + 'anywhere else unless you connect a provider yourself.',
  question: 'Where does Gideon keep my data?',
} as const

export const REMINDER_SEED = {
  name: 'Daily check-in',

  cron: '0 9 * * *',
  title: 'Your daily check-in',
  body: 'What is worth handing to your agent today?',
} as const

export const LOOP_SEED = {

  kind: 'general',
  task: 'Draft a short note describing what I could use an agent for this week.',
} as const

export function failureText(error: unknown): string {
  const candidate = error instanceof Error ? error.message : String(error ?? '')
  return candidate.trim() || 'The call failed without a message.'
}

const PROVIDER_MARKERS = [
  /\bunauthor/i, /\bforbidden\b/i, /\bauthenticat/i, /\bcredential/i,
  /\bapi[ _-]?key\b/i, /\btoken\b.*\b(invalid|expired|revoked)\b/i,
  /\bno provider\b/i, /\bprovider\b.*\b(not|fail|refus|unavailable|error)/i,
  /\bquota\b/i, /\brate limit/i, /\bbilling\b/i, /\binsufficient\b/i,

  /\bmodel\b.*\b(not found|not available|does not exist|unknown|unsupported|no access|access to)\b/i,
  /\bcould not resolve\b/i, /\bno (chat )?model\b/i,
]

export function isProviderFailure(message: string, status?: number): boolean {
  const credentialStatuses = new Set([401, 402, 403])
  const normalized = message.split(/_+/).join(' ')
  return credentialStatuses.has(status ?? 0) || PROVIDER_MARKERS.find(expression => expression.test(normalized)) !== undefined
}

export interface SettingsTarget {

  path: string
  label: string

  because: string
}

export function settingsTargetFor(message: string, status?: number): SettingsTarget {
  const destinations: Record<'provider' | 'doctor', SettingsTarget> = {
    provider: { path: 'settings/providers', label: 'Open model provider settings', because: 'The provider passed its test and then refused this call — its key or plan is what to check.' },
    doctor: { path: 'settings/doctor', label: 'Open Settings → Doctor', because: 'Doctor checks the parts of your install this call depends on.' },
  }
  return destinations[isProviderFailure(message, status) ? 'provider' : 'doctor']
}

function outcome(headline: string, href: string, linkLabel: string, facts: Array<[string, string]>): TryOneOutcome {
  return { headline, href, linkLabel, facts: facts.map(([label, value]) => ({ label, value })) }
}
function passage(text: string): string {
  const normalized = text.split(/\s+/).filter(Boolean).join(' ')
  return normalized.length <= 200 ? normalized : normalized.slice(0, 199) + '…'
}
export async function runKnowledgeFlow(): Promise<TryOneOutcome> {
  const { id } = await api.createKnowledgeItem({ type: 'note', title: KNOWLEDGE_SEED.title, content: KNOWLEDGE_SEED.content, tags: ['first-run'] })
  const result = await api.knowledgeSearchForContext(KNOWLEDGE_SEED.question, 1200)
  const matches = result.results.filter(entry => entry.id === id)
  if (!matches.length) throw new Error(`The note saved (${id}) but asking "${KNOWLEDGE_SEED.question}" did not return it. The knowledge index is not answering.`)
  const answer = matches[0]
  return outcome('Your note is in Knowledge — and it answered a question about itself.', `knowledge/item/${id}`, 'Open the note', [
    ['You asked', KNOWLEDGE_SEED.question], ['It answered from', answer.title],
    ['The passage', passage(answer.content || answer.summary || '')], ['Matched by', `${answer.match_type || 'search'} · ${answer.tokens} tokens of context`],
  ])
}
export async function runReminderFlow(): Promise<TryOneOutcome> {
  const { trigger } = await api.createSchedule({ name: REMINDER_SEED.name, cron: REMINDER_SEED.cron,
    action: { provider: 'notify', config: { title_template: REMINDER_SEED.title, body_template: REMINDER_SEED.body, kind: 'success' } },
  })
  const execution = await api.runSchedule(trigger.raw_id)
  if (!execution.ok) {
    const detail = typeof execution.result === 'string' ? execution.result : ''
    throw new Error(execution.refused || detail || 'The reminder was created but firing it did not run the action.')
  }
  const feed = await api.notifications()
  const notice = feed.notifications.filter(entry => entry.title === REMINDER_SEED.title)[0]
  if (!notice) throw new Error(`The reminder fired but no "${REMINDER_SEED.title}" notification reached the dashboard.`)
  const next = trigger.next_run_ts ? new Date(trigger.next_run_ts * 1000).toLocaleString() : 'not scheduled yet'
  return outcome('Your reminder is set — and this is what it will say.', 'notifications', 'See it in notifications', [
    ['It said', `${notice.title} — ${notice.body}`], ['Cadence', trigger.schedule || REMINDER_SEED.cron], ['Next time', next],
  ])
}
export async function runLoopFlow(): Promise<TryOneOutcome> {
  const created = await api.createULoop({ ...LOOP_SEED, max_cycles: 1 })
  const running = await api.uLoopAction(created.id, 'start')
  if (running.status !== 'running') throw new Error(`The loop was created but did not start — it is "${running.status}".`)
  return outcome('Your first loop is running.', `loops/${running.id}`, 'Watch it work', [
    ['Working on', running.task || LOOP_SEED.task], ['Status', running.status], ['Budget', `${running.max_cycles} cycle — it stops on its own`],
  ])
}

export const TRY_ONE_FLOWS: Record<TryOneId, () => Promise<TryOneOutcome>> = {
  knowledge: runKnowledgeFlow,
  trigger: runReminderFlow,
  loop: runLoopFlow,
}
