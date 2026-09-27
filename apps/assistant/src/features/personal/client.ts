import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type { OwnerScope } from '../../shared/auth.web'

export type PersonalSourceKind = 'idea' | 'human-goal' | 'goal-plan' | 'goal-session' | 'identity-story' | 'learning-capture' | 'learning-review' | 'companion-item' | 'memory-fact' | 'health-measurement' | 'journal-entry'
export type PersonalIdentity = Readonly<{ ownerScopeKey: string; sourceKind: PersonalSourceKind; nativeId: string }>
export type PersonalRecord<T> = Readonly<{ identity: PersonalIdentity; value: T; revision?: number; freshness: 'current' | 'stale' }>
export type PersonalAvailability<T> = Readonly<{ state: 'available'; value: T }> | Readonly<{ state: 'unavailable'; reason: string }>
export type PersonalWrite<T> = Readonly<{ value: T; requestId: string; expectedRevision?: number }>
export type NativeValue = Readonly<Record<string, unknown>>
export type IdeaRecord = Readonly<{ id: string; title: string; revision?: number; status?: string; source_link?: string } & NativeValue>
export type HumanGoal = Readonly<{ id: string; title: string; description: string; status: 'active' | 'completed' | 'archived'; target_date: string | null; revision: number } & NativeValue>
export type HumanGoalInput = Readonly<{ title: string; description?: string; status?: 'active' | 'completed' | 'archived'; target_date?: string | null }>
export type GoalMilestone = Readonly<{ id: string; title: string; done: boolean; target_date: string | null }>
export type GoalPlan = Readonly<{ goal_id: string; revision: number; parent_id: string | null; horizon: 'short_term' | 'long_term' | 'lifetime'; milestones: readonly GoalMilestone[]; links: readonly Readonly<{ kind: 'task' | 'loop' | 'session'; id: string; title?: string | null; status?: string | null; availability?: string }>[]; unit: string; target_value: number | null } & NativeValue>
export type GoalPlanProjection = Readonly<{ goal: HumanGoal; plan: GoalPlan; children: readonly string[]; checkins: readonly NativeValue[]; velocity: NativeValue | null; linked_sources: readonly NativeValue[]; milestones_complete_ratio: number | null }>
export type GoalSession = Readonly<{ id: string; goal_id: string; title: string; start_at: string; end_at: string; status: 'scheduled' | 'completed' | 'cancelled'; notes: string; revision: number } & NativeValue>
export type IdentityStory = Readonly<{ id: string; prompt: string; theme: string; text: string; parent_id: string | null; revision: number } & NativeValue>
export type LearningCapture = Readonly<{ id: string; status: string; original_text: string; captured_at: string } & NativeValue>
export type LearningReview = Readonly<{ id: string; date?: string; status?: string } & NativeValue>
export type MemoryFact = Readonly<{ key: string; value: unknown; source?: string; confidence?: number } & NativeValue>
export type HealthMeasurement = Readonly<{ id: string; kind: string; observed_at: string; revision?: number } & NativeValue>
export type JournalRecord = Readonly<{ id: string; title: string; content: string; revision: number; fingerprint: string } & NativeValue>
export type JournalSnapshot = Readonly<{ date: string; timezone: string; journal: PersonalRecord<JournalRecord> | null }>

const clients = new Map<string, Set<PersonalClient>>()
const activeOwnerByOrigin = new Map<string, string>()
const enc = (value: string) => encodeURIComponent(value)

function requireId(id: string): void {
  if (!id.trim() || id.length > 512 || /[\u0000-\u001f\u007f]/.test(id)) throw new TypeError('A canonical native record ID is required')
}

function record<T extends NativeValue>(scope: OwnerScope, sourceKind: PersonalSourceKind, value: T): PersonalRecord<T> {
  const id = value.id ?? value.key ?? value.goal_id
  if (typeof id !== 'string' || !id) throw new TypeError(`Native ${sourceKind} record has no canonical ID`)
  return Object.freeze({ identity: Object.freeze({ ownerScopeKey: scope.cacheKey, sourceKind, nativeId: id }), value,
    ...(typeof value.revision === 'number' ? { revision: value.revision } : {}), freshness: 'current' })
}

type Entry = PersonalRecord<NativeValue>

export class PersonalClient {
  private readonly cache = new Map<string, Entry>()
  private generation = 0
  constructor(readonly scope: OwnerScope) {
    const previous = activeOwnerByOrigin.get(scope.runtimeOrigin)
    if (previous && previous !== scope.cacheKey) clearPersonalOwnerCache(previous)
    activeOwnerByOrigin.set(scope.runtimeOrigin, scope.cacheKey)
    const owners = clients.get(scope.cacheKey) ?? new Set<PersonalClient>()
    owners.add(this)
    clients.set(scope.cacheKey, owners)
  }

  clear(): void { this.generation++; this.cache.clear() }
  dispose(): void {
    this.clear()
    const owners = clients.get(this.scope.cacheKey)
    owners?.delete(this)
    if (!owners?.size) clients.delete(this.scope.cacheKey)
  }

  private async read<T>(path: string, sourceKind: PersonalSourceKind, signal?: AbortSignal): Promise<readonly PersonalRecord<T & NativeValue>[]> {
    const generation = this.generation
    let payload: unknown
    try { payload = await gatewayJson<unknown>(path, { signal }) }
    catch (error) {
      if (generation !== this.generation) throw new Error('Personal account changed while records were loading; refresh this selection')
      const cached = [...this.cache.values()].filter(item => item.identity.sourceKind === sourceKind)
      if (cached.length) return cached.map(item => Object.freeze({ ...item, freshness: 'stale' as const })) as unknown as readonly PersonalRecord<T & NativeValue>[]
      throw error
    }
    if (generation !== this.generation) throw new Error('Personal account changed while records were loading; refresh this selection')
    const rows: unknown[] = Array.isArray(payload) ? payload : payload && typeof payload === 'object'
      ? Object.values(payload).find(value => Array.isArray(value)) as unknown[] ?? [] : []
    if (payload && !Array.isArray(payload) && !rows.length && !Object.values(payload).some(Array.isArray)) {
      throw new TypeError(`Native ${sourceKind} collection did not return records`)
    }
    const result = rows.map(value => record<T & NativeValue>(this.scope, sourceKind, value as T & NativeValue))
    for (const item of result) this.cache.set(`${item.identity.sourceKind}:${item.identity.nativeId}`, item as Entry)
    return result
  }

  private async detail<T>(path: string, id: string, sourceKind: PersonalSourceKind, signal?: AbortSignal): Promise<PersonalRecord<T & NativeValue>> {
    requireId(id)
    const generation = this.generation
    const value = await gatewayJson<T>(path, { signal })
    if (generation !== this.generation) throw new Error('Personal account changed while the record was loading; refresh this selection')
    const result = record<T & NativeValue>(this.scope, sourceKind, value as T & NativeValue)
    if (result.identity.nativeId !== id) throw new Error('Native response returned a different canonical record ID')
    this.cache.set(`${sourceKind}:${id}`, result as Entry)
    return result
  }

  private async write<T>(path: string, method: 'POST' | 'PUT' | 'DELETE', body: NativeValue, signal?: AbortSignal): Promise<T> {
    const generation = this.generation
    const value = await gatewayJson<T>(path, { method, body, signal })
    if (generation !== this.generation) throw new Error('Personal account changed while the write was in progress; reload before continuing')
    return value
  }
  private async scopedRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
    const generation = this.generation
    try {
      const value = await gatewayJson<T>(path, { signal })
      if (generation !== this.generation) throw new Error('Personal account changed while the request was in progress; refresh this selection')
      return value
    } catch (error) {
      if (generation !== this.generation) throw new Error('Personal account changed while the request was in progress; refresh this selection')
      throw error
    }
  }

  async readIdeas(signal?: AbortSignal): Promise<readonly PersonalRecord<IdeaRecord>[]> {
    return this.read('/api/capabilities/knowledge/ideas', 'idea', signal)
  }
  async readIdea(id: string, signal?: AbortSignal): Promise<PersonalRecord<IdeaRecord>> {
    return this.detail<IdeaRecord>(`/api/capabilities/knowledge/ideas/${enc(id)}`, id, 'idea', signal)
  }
  async importIdeaList(input: NativeValue, signal?: AbortSignal): Promise<NativeValue> {
    return this.write('/api/capabilities/knowledge/ideas/import', 'POST', input, signal)
  }
  async scheduleIdeaSync(id: string, input: NativeValue, signal?: AbortSignal): Promise<NativeValue> {
    requireId(id)
    return this.write(`/api/capabilities/knowledge/ideas/${enc(id)}/schedule`, 'POST', input, signal)
  }

  async readGoals(signal?: AbortSignal): Promise<readonly PersonalRecord<HumanGoal>[]> {
    return this.read('/api/capabilities/identity/goals/goals', 'human-goal', signal)
  }
  async readGoal(id: string, signal?: AbortSignal): Promise<PersonalRecord<HumanGoal>> {
    return this.detail<HumanGoal>(`/api/capabilities/identity/goals/goals/${enc(id)}`, id, 'human-goal', signal)
  }
  async saveGoal(id: string | undefined, fields: HumanGoalInput, requestId: string, expectedRevision: number, signal?: AbortSignal): Promise<PersonalRecord<HumanGoal>> {
    if (!requestId.trim() || !Number.isInteger(expectedRevision) || expectedRevision < 0) throw new TypeError('A request ID and expected goal revision are required')
    const value = await this.write<NativeValue>(`/api/capabilities/identity/goals/goals`, 'POST', { ...fields, ...(id ? { id } : {}), request_id: requestId, expected_revision: expectedRevision }, signal)
    const result = record<HumanGoal>(this.scope, 'human-goal', value as HumanGoal)
    this.cache.set(`human-goal:${result.identity.nativeId}`, result)
    return result
  }
  async readGoalPlans(signal?: AbortSignal): Promise<readonly PersonalRecord<GoalPlan>[]> {
    const rows = await this.scopedRequest<unknown>('/api/capabilities/identity/goal-plans', signal)
    if (!Array.isArray(rows)) throw new TypeError('Native goal-plan collection did not return records')
    return rows.map(value => this.goalPlanRecord(value))
  }
  private goalPlanRecord(value: unknown): PersonalRecord<GoalPlan> {
    if (!value || typeof value !== 'object' || !('goal' in value) || !('plan' in value)) throw new TypeError('Native goal-plan projection is malformed')
    const projection = value as GoalPlanProjection
    if (!projection.goal || typeof projection.goal.id !== 'string' || projection.plan?.goal_id !== projection.goal.id) throw new TypeError('Native goal-plan projection returned mismatched goal identity')
    return Object.freeze({ identity: Object.freeze({ ownerScopeKey: this.scope.cacheKey, sourceKind: 'goal-plan', nativeId: projection.goal.id }), value: projection.plan,
      ...(typeof projection.plan.revision === 'number' ? { revision: projection.plan.revision } : {}), freshness: 'current' })
  }
  async readGoalPlan(id: string, signal?: AbortSignal): Promise<PersonalRecord<GoalPlanProjection>> {
    requireId(id)
    const projection = await this.scopedRequest<GoalPlanProjection>(`/api/capabilities/identity/goal-plans/${enc(id)}`, signal)
    if (!projection?.goal || projection.goal.id !== id || projection.plan?.goal_id !== id) throw new Error('Native response returned a different canonical goal ID')
    return Object.freeze({ identity: Object.freeze({ ownerScopeKey: this.scope.cacheKey, sourceKind: 'goal-plan', nativeId: id }), value: projection,
      ...(typeof projection.plan.revision === 'number' ? { revision: projection.plan.revision } : {}), freshness: 'current' })
  }
  async configureGoalPlan(input: NativeValue, signal?: AbortSignal): Promise<GoalPlan> {
    const result = await this.write<GoalPlan>('/api/capabilities/identity/goal-plans/configure', 'POST', input, signal)
    if (typeof input.goal_id !== 'string' || result.goal_id !== input.goal_id) throw new Error('Native response returned a different canonical goal ID')
    return result
  }
  async addGoalCheckin(input: NativeValue, signal?: AbortSignal): Promise<NativeValue> {
    const result = await this.write<NativeValue>('/api/capabilities/identity/goal-plans/checkins', 'POST', input, signal)
    if (typeof input.goal_id !== 'string' || result.goal_id !== input.goal_id) throw new Error('Native response returned a different canonical goal ID')
    return result
  }
  async readGoalSessions(goalId: string, signal?: AbortSignal): Promise<readonly PersonalRecord<GoalSession>[]> {
    requireId(goalId)
    const rows = await this.scopedRequest<GoalSession[] | { sessions: GoalSession[] }>(`/api/capabilities/identity/goals/sessions?goal_id=${enc(goalId)}`, signal)
    const sessions = Array.isArray(rows) ? rows : rows.sessions
    return sessions.filter(session => session.goal_id === goalId).map(session => record(this.scope, 'goal-session', session))
  }
  async saveGoalSession(input: NativeValue, signal?: AbortSignal): Promise<PersonalRecord<GoalSession>> {
    if (typeof input.goal_id !== 'string' || typeof input.request_id !== 'string') throw new TypeError('Goal sessions require their canonical goal ID and request ID')
    const value = await this.write<GoalSession>('/api/capabilities/identity/goals/sessions', 'POST', input)
    if (value.goal_id !== input.goal_id) throw new Error('Native response returned a different canonical goal ID')
    return record(this.scope, 'goal-session', value)
  }
  async readGoalSources(signal?: AbortSignal): Promise<readonly Readonly<{ kind: 'task' | 'loop'; id: string; title: string; status: string }>[]> {
    const optional = async (path: string) => {
      try { return await this.scopedRequest<unknown>(path, signal) }
      catch (error) { if (error instanceof GatewayError && [404, 501].includes(error.status)) return []; throw error }
    }
    const [tasks, loops] = await Promise.all([optional('/api/tasks'), optional('/api/loops')])
    const values = (input: unknown, key: string): NativeValue[] => Array.isArray(input) ? input.filter((row): row is NativeValue => !!row && typeof row === 'object')
      : input && typeof input === 'object' && key in input && Array.isArray((input as NativeValue)[key]) ? ((input as NativeValue)[key] as NativeValue[]) : []
    const taskRows = values(tasks, 'tasks').flatMap(row => typeof row.id === 'string' && typeof row.title === 'string' ? [{ kind: 'task' as const, id: row.id, title: row.title, status: String(row.status ?? 'unknown') }] : [])
    const loopRows = values(loops, 'loops').flatMap(row => typeof row.id === 'string' && typeof row.name === 'string' ? [{ kind: 'loop' as const, id: row.id, title: row.name, status: String(row.status ?? 'unknown') }] : [])
    return [...taskRows, ...loopRows]
  }

  async readIdentityStories(signal?: AbortSignal): Promise<readonly PersonalRecord<IdentityStory>[]> {
    return this.read('/api/capabilities/identity/stories', 'identity-story', signal)
  }
  async readIdentityStory(id: string, signal?: AbortSignal): Promise<PersonalRecord<IdentityStory>> {
    return this.detail<IdentityStory>(`/api/capabilities/identity/stories/${enc(id)}`, id, 'identity-story', signal)
  }
  async saveIdentityStory(id: string | undefined, input: NativeValue, requestId: string, expectedRevision: number, signal?: AbortSignal): Promise<PersonalRecord<NativeValue>> {
    if (!requestId.trim()) throw new TypeError('A native request ID is required')
    const value = await this.write<NativeValue>(id ? `/api/capabilities/identity/stories/${enc(id)}` : '/api/capabilities/identity/stories', id ? 'PUT' : 'POST',
      { ...input, ...(id ? { expected_revision: expectedRevision } : { request_id: requestId }) }, signal)
    const result = record(this.scope, 'identity-story', value)
    this.cache.set(`identity-story:${result.identity.nativeId}`, result)
    return result
  }
  async readIdentityProfile(signal?: AbortSignal): Promise<NativeValue> {
    return this.scopedRequest('/api/capabilities/identity/twin', signal)
  }

  async readLearningCaptures(signal?: AbortSignal): Promise<PersonalAvailability<readonly PersonalRecord<LearningCapture>[]>> {
    return this.optionalRead<LearningCapture>('/api/capabilities/knowledge/captures', 'learning-capture', signal)
  }
  async readLearningReviews(signal?: AbortSignal): Promise<PersonalAvailability<readonly PersonalRecord<LearningReview>[]>> {
    return this.optionalRead<LearningReview>('/api/capabilities/knowledge/reviews', 'learning-review', signal)
  }
  async saveLearningReview(input: NativeValue, signal?: AbortSignal): Promise<NativeValue> {
    return this.write('/api/capabilities/knowledge/reviews', 'POST', input, signal)
  }
  private async optionalRead<T extends NativeValue>(path: string, kind: PersonalSourceKind, signal?: AbortSignal): Promise<PersonalAvailability<readonly PersonalRecord<T>[]>> {
    try { return { state: 'available', value: await this.read(path, kind, signal) } }
    catch (error) {
      if (error instanceof GatewayError && [404, 501].includes(error.status)) return { state: 'unavailable', reason: 'This native personal service is unavailable on this Gideon instance.' }
      throw error
    }
  }
  async createLearningCapture(text: string, requestId: string, signal?: AbortSignal): Promise<PersonalRecord<NativeValue>> {
    if (!text.trim() || !requestId.trim()) throw new TypeError('Capture text and request ID are required')
    const value = await this.write<NativeValue>('/api/capabilities/knowledge/captures', 'POST', { text, request_id: requestId }, signal)
    return record(this.scope, 'learning-capture', value)
  }

  async readCompanion(): Promise<PersonalAvailability<NativeValue>> {
    try { return { state: 'available', value: await this.scopedRequest('/api/companion/discovery') } }
    catch (error) { if (error instanceof GatewayError && [404, 501].includes(error.status)) return { state: 'unavailable', reason: 'Companion is unavailable on this Gideon instance.' }; throw error }
  }

  async readMemory(signal?: AbortSignal): Promise<readonly PersonalRecord<MemoryFact>[]> {
    return this.read('/api/memory/semantic', 'memory-fact', signal)
  }
  async readMemoryFact(id: string, signal?: AbortSignal): Promise<PersonalRecord<MemoryFact>> {
    requireId(id)
    const rows = await this.readMemory()
    const match = rows.find(row => row.identity.nativeId === id)
    if (!match) throw new GatewayError('Memory record not found', 404, 'not_found')
    return match
  }
  async saveMemoryFact(input: MemoryFact, signal?: AbortSignal): Promise<PersonalRecord<MemoryFact>> {
    if (typeof input.key !== 'string' || !input.key) throw new TypeError('Memory writes require the native key')
    await this.write<NativeValue>('/api/memory/semantic', 'PUT', input, signal)
    return record(this.scope, 'memory-fact', { ...input, id: input.key })
  }
  async forgetMemoryFact(id: string, signal?: AbortSignal): Promise<NativeValue> {
    requireId(id)
    const generation = this.generation
    const value = await gatewayJson<NativeValue>(`/api/memory/semantic/${enc(id)}`, { method: 'DELETE', signal })
    if (generation !== this.generation) throw new Error('Personal account changed while memory was being updated')
    this.cache.delete(`memory-fact:${id}`)
    return value
  }

  async readHealthMeasurements(signal?: AbortSignal): Promise<readonly PersonalRecord<HealthMeasurement>[]> {
    const result = await this.scopedRequest<{ measurements: HealthMeasurement[] }>('/api/capabilities/wellbeing/measurements', signal)
    return result.measurements.map(value => record<HealthMeasurement>(this.scope, 'health-measurement', value))
  }
  async readHealthMeasurement(id: string, signal?: AbortSignal): Promise<PersonalRecord<HealthMeasurement>> {
    return this.detail<HealthMeasurement>(`/api/capabilities/wellbeing/measurements/${enc(id)}`, id, 'health-measurement', signal)
  }
  async createHealthMeasurement(input: NativeValue, signal?: AbortSignal): Promise<PersonalRecord<HealthMeasurement>> {
    const value = await this.write<NativeValue>('/api/capabilities/wellbeing/measurements', 'POST', input, signal)
    return record<HealthMeasurement>(this.scope, 'health-measurement', value as HealthMeasurement)
  }

  async readJournal(date: string, timezone: string, signal?: AbortSignal): Promise<PersonalAvailability<JournalSnapshot>> {
    const query = new URLSearchParams({ date, timezone })
    try {
      const value = await this.scopedRequest<{ date: string; timezone: string; journal: JournalRecord | null }>(`/api/capabilities/knowledge/journals?${query}`, signal)
      return { state: 'available', value: { ...value, journal: value.journal ? record(this.scope, 'journal-entry', value.journal) : null } }
    }
    catch (error) { if (error instanceof GatewayError && [404, 501].includes(error.status)) return { state: 'unavailable', reason: 'Journal is unavailable on this Gideon instance.' }; throw error }
  }
  async readJournalDraft(date: string, timezone: string, signal?: AbortSignal): Promise<NativeValue> {
    const query = new URLSearchParams({ date, timezone })
    return this.scopedRequest(`/api/capabilities/knowledge/journals/draft?${query}`, signal)
  }
  async saveJournal(input: NativeValue, signal?: AbortSignal): Promise<NativeValue> {
    if (typeof input.request_id !== 'string' || !input.request_id || typeof input.revision !== 'number') throw new TypeError('Journal writes require their native request ID and revision')
    return this.write('/api/capabilities/knowledge/journals', 'POST', input, signal)
  }
}

export function createPersonalClient(scope: OwnerScope): PersonalClient { return new PersonalClient(scope) }
export function clearPersonalOwnerCache(cacheKey: string): void {
  for (const client of clients.get(cacheKey) ?? []) client.clear()
  for (const [origin, active] of activeOwnerByOrigin) if (active === cacheKey) activeOwnerByOrigin.delete(origin)
}
