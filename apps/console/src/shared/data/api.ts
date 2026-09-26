import { errText } from './errText'
import { gatewayHeaders as SK, gatewayRequest, requestJson, requestDelete, readJson as j, responseError as apiError } from './gatewayRequest'
import { emitTaskListCreated } from './taskListCount'
export { ApiError, hasApiCode } from './gatewayRequest'

const get = <T>(path: string) => requestJson<T>(path)
const post = <T>(path: string, body?: unknown) => requestJson<T>(path, 'POST', body)
const put = <T>(path: string, body?: unknown) => requestJson<T>(path, 'PUT', body)
const patch = <T>(path: string, body?: unknown) => requestJson<T>(path, 'PATCH', body)
const del = requestDelete

type WriteReceipt = {
  ok?: boolean
  message?: string
  reason?: string
  error?: string | { message?: string }
}

export function requireWriteAccepted<T>(result: T): T {
  if (!result || typeof result !== 'object' || (result as WriteReceipt).ok !== false) return result
  const receipt = result as WriteReceipt
  const detail = typeof receipt.error === 'string' ? receipt.error : receipt.error?.message
  throw new Error(detail || receipt.message || receipt.reason || 'The server refused this change.')
}

async function _installReq(path: string, body: unknown): Promise<AppInstallResult> {
  const failure = (error: string): AppInstallResult => ({ ok: false, name: '', error, needs_consent: false, scan: null })
  try {
    const response = await gatewayRequest(path, 'POST', body)
    const result: unknown = await response.json().catch(() => undefined)
    return result && typeof result === 'object' ? result as AppInstallResult : failure(`HTTP ${response.status}`)
  } catch (error) {
    return failure(error instanceof Error ? error.message : String(error))
  }
}

export interface ThemeSummary { slug: string; name: string; emoji: string; created_at: string }
export interface ThemeRecord extends ThemeSummary {
  dark: Record<string, string>
  light: Record<string, string>
}
export interface ThemeWrite {
  name: string; emoji?: string
  dark: Record<string, string>
  light: Record<string, string>
}
export interface ProviderHealth {
  name: string
  breaker_state: 'closed' | 'open' | 'half_open'
  consecutive_failures: number
  calls: number
  passed: number
  failed: number
  pass_rate: number | null
  p50_ms: number
  p90_ms: number
  p99_ms: number
  failure_modes: Record<string, number>
  degraded: boolean
}

export interface CallerHealth {
  name: string
  calls: number
  passed: number
  failed: number
  pass_rate: number | null
  p50_ms: number
  p90_ms: number
  p99_ms: number
  failure_modes: Record<string, number>
  dollars_est: number
}

export interface AutonomyType {
  key: string
  floor: string
  ceiling: string
  leaves_machine: boolean
  providers: string[]
  resolved_rung: string
  granted_rung: string
  held_by_incident: boolean
  authority: string
  granted_at: string
  evidence_window: string
  demotions: Array<{ at: string; cause: string; cooldown_until: string }>
  eligible: boolean
  next_rung: string
  record: string
  clean_approvals: number
  rejections: number
  observed_days: number
  cooldown_until: string
}
export interface AutonomyReversal {
  id: string
  action_type: string
  rung: string
  label: string
  created_at: string
  reversed_at: string
}
export interface AutonomyLadder {
  rungs: string[]
  rung_meta: Array<{ key: string; label: string; hint: string }>
  incident_active: boolean
  types: AutonomyType[]
  reversals: AutonomyReversal[]
}

export interface ExternalAccessSurface {
  surface: string
  enabled: boolean
  allow_remote: boolean
  token_configured: boolean
  token_problem: string
  loopback_only: boolean
}
export interface ExternalAccessClient {
  client_id: string
  label: string
  surfaces: string[]
  agent: string
  tools: string[]
  scope: Record<string, unknown>
  rate_overrides: Record<string, unknown>
  disabled: boolean
  created_at: string
  last_seen_at: string
  requests_seen: number
  refusals_seen: number
}
export interface ExternalAccess {
  enabled: boolean
  incident_active: boolean
  public_url: string
  caps: {
    rate_rps?: number
    rate_burst?: number
    rate_concurrent?: number
    auto_disable_after_breaches?: number
    capture_retention_days?: number
    capture_upstream_allowlist?: string[]
  }
  surfaces: ExternalAccessSurface[]
  clients: ExternalAccessClient[]
}

export interface DoctorProbe {
  id: string
  capability: string
  tier: number
  title: string
  ok: boolean
  detail: string
  evidence: Record<string, unknown>
  fix_id?: string
}
export interface DoctorCapability {
  ok: boolean
  tier: number
  probes: DoctorProbe[]
}
export interface DoctorReport {
  ok: boolean
  core_ok: boolean
  worst: string
  restart_suggested: boolean
  capabilities: Record<string, DoctorCapability>
  skipped_capabilities: string[]
  generated_at: number
}

export interface DegradedSurface {
  surface: string
  available: boolean
  floor: string
  backlog: number
  use_cases: string[]
}
export interface DurabilityJob {
  last_run: number
  due_in_secs: number
  due: boolean
}
export interface DurabilitySyncStatus extends DurabilityJob {
  enabled: boolean
  transport: string
  encrypt: 'auto' | 'on' | 'off' | string
  encrypted: boolean
}
export interface DurabilityStatus {
  enabled: boolean
  export: DurabilityJob
  snapshot: DurabilityJob
  drill: DurabilityJob
  sync: DurabilitySyncStatus
}
export interface DurabilityConflict {
  id: string
  entry_id: string
  entity_id: string
  domain: string
  surface: string
  ancestor_sha: string
  local_sha: string
  remote_sha: string
  local_row: Record<string, unknown>
  remote_row: Record<string, unknown>
  detected_at: string
  status: string
  proposal: Record<string, unknown> | null
  rationale: string
  proposed_at: string
  proposal_error: string
  resolution: string
  resolved_at: string
}
export interface DurabilityHistoryRoot {
  id: string
  label: string
  worktree: string
  exists: boolean
  commits: number
  memory: boolean
}
export interface DurabilityHistoryStatus {
  enabled: boolean
  git: boolean
  dir: string
  roots: DurabilityHistoryRoot[]
}
export interface DurabilityHistoryEntry {
  sha: string
  short: string
  at: number
  subject: string
  surface: string
  unattended: boolean
}
export interface DurabilityHistoryTimeline {
  root: string
  label: string
  commits: number
  entries: DurabilityHistoryEntry[]
  forward_refs: { ref: string; sha: string; at: number }[]
}
export interface DurabilityHistoryDiffFile {
  path: string
  status: string
  bytes: number
  rendered: boolean
  diff: string
}
export interface DurabilityHistoryPreview {
  operation: 'rollback' | 'revert'
  root: string
  target: string
  head: string
  files: DurabilityHistoryDiffFile[]
  commits_rolled_away: number
  reversible: boolean
  paths?: string[]
}
export interface DurabilityHistoryPreviewResponse {
  confirmed: boolean
  expected_head: string
  preview: DurabilityHistoryPreview
}
export interface DurabilityHistoryResult {
  ok: boolean
  operation: string
  root: string
  head: string
  prior_head?: string
  prior_ref?: string
  reverted?: string
  reload_required: boolean
  paths?: string[]
}
export interface DurabilityConflicts {
  conflicts: DurabilityConflict[]
  truncated: boolean
  counts: {
    total: number
    needs_review: number
    by_surface: Record<string, number>
    selected: number
  }
  surfaces: { memory: string; knowledge: string; durability: string }
  sync: { enabled: boolean; transport: string; configured: boolean }
}
export type DurabilityConflictChoice = 'keep_local' | 'take_remote' | 'accept_proposal'
export type DurabilityDomainCounts = Record<string, { files: number; bytes: number; rows: number }>
export interface DurabilityDrill {
  ran: boolean
  ok: boolean | null
  at: number
  detail: string
  archive: string
  databases_checked?: number
}
export interface DurabilityArchive {
  id: string
  name: string
  taken_at: string
  size: number
  retained: boolean
  domains: DurabilityDomainCounts | null
  validate: DurabilityDrill | null
}
export interface DurabilityArchives {
  directory: string
  archives: DurabilityArchive[]
  would_prune: string[]
  tiers: { daily: number; weekly: number; monthly: number }
  last_drill: DurabilityDrill
}
export interface DurabilityJobResult {
  job: string
  ok: boolean
  skipped: string
  detail: string
  duration_secs: number
  extra?: Record<string, unknown>
}
export interface DegradedReport {
  surfaces: DegradedSurface[]
  degraded: string[]
}

export interface DoctorFix {
  id: string
  title: string
  impact: string
  preview: string
}
export interface SurfacingCandidate {
  key: string
  kw_score: number
  sem_score: number
  threshold_kw: number
  threshold_sem: number
  negated: boolean
  included: boolean
  reason: string
}

export interface AutomationNextFire {
  cadence: string
  at: string
  epoch: number | null
  source: 'armed' | 'computed' | 'none'
  armed: boolean
}
export interface AutomationActionConfig {
  provider: string
  config: Record<string, unknown>
  vars: Record<string, unknown>
  secret_refs: string[]
  rendered: string
  render_error: string
}
export interface AutomationCapabilityGrants {
  declared: Record<string, string[]>
  requested: Record<string, string[]>
  needs_fence: Record<string, string[]>
  refused: { key: string; value: string; reason: string }[]
  granted: boolean
}
export interface AutomationObserveMode {
  provider: string
  provider_known: boolean
  supported: boolean
  mode: 'observe' | 'preview'
  executed: boolean
  ok: boolean
  detail: string
  gate_plan: { enforced?: string[]; bypassed?: string[]; dry_run?: boolean; executes?: boolean }
}
export interface AutomationWouldExecute {
  trigger: {
    id: string; name: string; kind: string; enabled: boolean; state: string; ok: boolean
    issues: { path: string; message: string; severity: string; closest: string }[]
  }
  next_fire: AutomationNextFire
  action_config: AutomationActionConfig
  session_key: { key: string; declared: string; mode: 'pinned' | 'conversation' | 'fresh' }
  capability_grants: AutomationCapabilityGrants
  observe_mode: AutomationObserveMode
  dry_run: boolean
}

export interface RemediationJobRow {
  id: string
  status: string
  cost: number
  detail?: string
  error?: string
}
export interface RemediationRun {
  ts: number
  score_before: number
  score_after: number
  jobs: RemediationJobRow[]
  stopped_reason: string
}
export interface RemediationSnapshot {
  score: number
  target_score: number
  deficits: { key: string; count: number; penalty: number; reachable: boolean }[]
  plan: RemediationJobRow[]
  recent_runs: RemediationRun[]
}

export interface ChannelHealth { state: string; detail?: string }
export interface ChannelRuntime {
  name: string; display_name: string; connected: boolean
  capabilities?: Record<string, unknown>
  health: ChannelHealth
}
export interface ChannelTrustSender {
  sender_id: string
  name: string
  added_at: string
  via: string
}
export interface ChannelTrustChannel { channel_id: string; name: string; added_at: string }
export interface ChannelTrustProvider {
  provider: string
  policies: { dm: string; group: string }
  allowed_senders: ChannelTrustSender[]
  tracked_channels: ChannelTrustChannel[]
  pairing_active: boolean
  pairing_expires_at: string
}
export interface ChannelTrust {
  providers: ChannelTrustProvider[]
  dm_policies: string[]
  group_policies: string[]
  default_dm_policy: string
  default_group_policy: string
}
export interface SpawnedAgent { id: string; task: string; done: boolean; parent?: string; agent?: string; started?: number; result?: string; error?: string }
export interface KnowledgeContextCard {
  id: string; title: string; provider?: string; match_type?: string; tokens: number; summary?: string
  content?: string
  source_type?: string | null; section?: string | null; line_range?: [number, number] | null; deep_link?: string | null
}
export interface KnowledgeContextResult { query: string; results: KnowledgeContextCard[]; total_tokens: number; max_tokens: number }
export interface RecallRankingScore {
  label: string
  kind: 'relative_ordering_signal'
  value: number | null
  shown: boolean
  is_probability: false
  comparable_across_queries: false
  explanation: string
}
export interface RecallRankingSignal {
  id: string
  label: string
  active: boolean
  applies_to: string[]
  detail: string
}
export interface RecallRankingDisclosure {
  method: string
  summary: string
  score: RecallRankingScore
  signals: RecallRankingSignal[]
}
export interface MemoryRecallResult {
  result: string
  query: string
  deep: boolean
  ranking: RecallRankingDisclosure
}

export interface LexiconTerm { id: string; canonical: string; aliases: string[]; entity_type: string; weight: number; source: 'graph' | 'manual' | 'learned' | string; enabled: boolean }
export interface LexiconCorrection { id: string; heard: string; meant: string; count: number; auto_apply: boolean; last_seen: string }
export interface McpActiveServer { name: string; enabled: boolean }
export interface AgentHook { command: string; matcher?: string; source?: string }
export interface AgentProvider {
  name: string; provider_id: string; type: string; ready: boolean; state: string; detail: string
}
export interface DiscoveredAgent {
  id: string; name: string; runtime: string; description: string; provider_agent: string; reasoning_effort: string; models: string[]
  supported_efforts?: { value: string; label: string }[]
}
export interface ModelItem { name: string; model_name: string; description: string; provider: string }

export interface AppPermissionsWire {
  api?: string[]; events?: string[]; mcpTools?: string[]
  storage?: boolean; network?: boolean; memory?: string; cron?: boolean; agent?: boolean
  appMessaging?: string[]
  storageShared?: boolean
  storageRead?: string[]
  desktop?: string[]
  proposals?: AppProposalKindWire[]
  backgroundTasks?: boolean
  eventSubscriptions?: string[]
}
export interface AppProposalKindWire { kind_suffix: string; label?: string }
export interface DesktopCapabilityWire {
  available: boolean
  granted: 'granted' | 'denied' | 'restricted' | 'not-determined' | 'unavailable'
  requestable: boolean
  reason: string
}
export interface DesktopStateWire {
  connected: boolean
  shell: { version: string; platform: string } | null
  capabilities: Record<string, DesktopCapabilityWire>
  registered_at: string
  last_seen: string
}
export interface CompanionDiscovery {
  advertising: boolean
  reason: 'advertising' | 'disabled' | 'loopback_only' | 'no_lan_address' | 'gateway_not_running'
  detail: string
  service_type: string
  instance_name: string
  port: number
  addresses: string[]
  txt: Record<string, string>
}
export interface DeviceRec {
  id: string
  name: string
  kind: 'browser' | 'mobile' | 'desktop' | 'cli' | 'unknown'
  minted_at: number
  last_seen: number
  issuer: string
  expires_at: number
}
export interface DevicePairStart {
  code: string
  pairing_url: string
  expires_at: number
  expires_in: number
}
export interface AppUiPage { route: string; label: string; icon: string }
export interface AppQualityWire {
  tested?: boolean
  designSystem?: 'v2' | 'legacy' | 'n/a'
  a11y?: boolean
}
export interface AppSummary {
  name: string; displayName: string; version: string; description: string
  enabled: boolean; origin: string; source?: string; icon: string
  sourceKind?: string
  heroUrl?: string
  hasBackend: boolean; hasUI: boolean
  uiPages: AppUiPage[]
  uiComponents?: string
  uiCapabilities?: string[]
  isProvider: boolean; providerType: string; hasConfig: boolean
  permissions: AppPermissionsWire
  tags: string[]
  installedAt?: string; updatedAt?: string
  backendRunning: boolean; backendPort: number | null
  native?: boolean
  updateAvailable?: boolean
  latestVersion?: string
  quality?: AppQualityWire
}
export interface AppDetail {
  name: string
  installed: Record<string, unknown>
  manifest: Record<string, unknown> | null
  config: Record<string, unknown>
  configSchema: Record<string, unknown>
  backendRunning: boolean; backendPort: number | null
}
export interface AppCronSummary {
  name: string; every?: number; cron_expr?: string; agent?: string; message?: string
  cadence?: string
}
export interface RegistryProvenance {
  maintainer?: string
  lastValidated?: string
  scanVerdict?: string
}
export interface AppCatalogEntry {
  registry?: RegistryProvenance | null
  name: string; displayName: string; description: string; version: string
  icon: string; heroUrl?: string; author: string
  source: string; sourceKind: 'bundled' | 'native' | 'first-party' | 'local' | 'git'
  isProvider: boolean; providerType: string; tags: string[]
  providerCapabilities?: string[]
  pointer?: string
  permissions?: AppPermissionsWire
  crons?: AppCronSummary[]
  hasUI?: boolean
  uiComponents?: string
  quality?: AppQualityWire
}
export interface AppCatalog {
  bundled: AppCatalogEntry[]
  gitSources: string[]
  defaultGitSources?: string[]
  builtinGitSources?: string[]
  localSources?: string[]
  firstPartySources?: string[]
  localApps?: AppCatalogEntry[]
  remoteApps?: AppCatalogEntry[]
  gitApps?: AppCatalogEntry[]
  networkSources?: string[]
}
export interface AppScanFinding { surface: string; severity: string; rule: string; path: string; evidence: string }
export interface AppSignature { state: string; signer: string; reason: string }
export interface AppScanReport {
  verdict: string; findings: AppScanFinding[]; tier?: string
  signature?: AppSignature | null
}
export interface AppInstallResult {
  ok: boolean; name: string; error: string; needs_consent: boolean
  scan: AppScanReport | null
  needs_client_install?: boolean
  client_install?: { shell?: string; postInstall?: string } | null
  restart_required?: boolean
  log_excerpt?: string
  fix_prompt?: string
}
export interface SkillInstallResult {
  ok?: boolean; path?: string; error?: string
  httpStatus: number
  verdict?: string
  tier?: string
  overridable?: boolean
  scan?: AppScanReport | null
}
export interface AppDepClassification {
  key: string; kind: string; id: string; disposition: string; remaining: string[]
}
export interface AppDataFacts { present: boolean; entries: number; path: string; unconsumed?: string[] }
export interface AgentDef { name: string }
export interface ChatSession {
  key: string; title: string; agent: string; model: string; reasoning_effort: string
  acp_provider: string; acp_provider_agent: string; mode: string; workspace_dir: string
  messages: number; running: boolean; stopping: boolean; pending_approval: boolean
  memory_mode?: string; last_message?: string
  last_ts?: string
}
export interface ChatSessionSummary {
  key: string; title: string; agent?: string; model?: string; messages: number
  running?: boolean; created?: string; last_activity_ts?: string; last_ts?: string; pinned?: boolean
  folder_id?: string; tags?: string[]; color_index?: number | null
  last_message?: string; prompt_preview?: string
  origin?: 'manual' | 'loop' | 'code' | 'campaign' | 'channel'
  source_id?: string; source_label?: string
  lifecycle?: 'active' | 'archived'
  last_activity_at?: number
  never_archive?: boolean
}
export interface AutoArchiveSessionsResult {
  ok: boolean
  enabled: boolean
  dry_run?: boolean
  days: number
  keys: string[]
  count: number
}
export type KnowledgeConflict = {
  item_id: string
  item_title: string
  left_claim: string
  right_claim: string
  left_item: string
  right_item: string
  kind: 'value' | 'polarity' | 'number'
  prefer: 'left' | 'right' | ''
  detail: string
  confidence: number
}

export type KnowledgeItemRelation = {
  item_id: string
  title: string
  relation: 'supersedes' | 'contradicts' | 'derived_from' | 'depends_on' | 'part_of'
  confidence: number
  provenance: 'extracted' | 'inferred'
}

export interface KnowledgeTag {
  id: number
  name: string
  parent_id: number | null
  parent_name: string | null
  usage_count: number
}
export type KnowledgeBulkOp =
  | 'collect' | 'uncollect' | 'read_state' | 'favorite' | 'archive' | 'restore' | 'pin'
export interface KnowledgeBulkResult {
  ok: boolean
  op: KnowledgeBulkOp
  changed: string[]
  unchanged: string[]
  missing: string[]
}
export interface KnowledgeCollection {
  id: string; name: string; kind: 'manual' | 'smart'; query?: string; icon?: string
  position?: number; item_count?: number | null; created_at?: string; updated_at?: string
}
export interface KnowledgeLibraryHome {
  recently_added: KnowledgeItem[]; continue_reading: KnowledgeItem[]; favorites: KnowledgeItem[]
  collections: { id: string; name: string; kind: 'manual' | 'smart'; icon?: string; position?: number; count: number; count_capped?: boolean }[]
}
export interface KnowledgeAnnotation {
  id: string; item_id: string; quote: string; occurrence: number; note: string; created_at: string
}
export interface KnowledgeReadingItem {
  item: KnowledgeItem
  annotations: KnowledgeAnnotation[]
}
export interface KnowledgeDuplicate {
  id: string; title: string; item_type: string; created_at: string; word_count: number; reason: string
}
export interface KnowledgeMergeResult {
  ok: boolean; kept: string; merged: string
  moved: {
    collections: number; tags: number; mentions: number; annotations: number
    relations: number; citations: number
  }
}
export interface KnowledgeSection {
  offset: number; line: number; title: string; level: number; chars: number
}
export interface KnowledgeRestructureBreak {
  kind: 'citation' | 'citation_chunk' | 'wikilink' | 'annotation' | 'kind_contract' | string
  message: string; relinkable: boolean; refs: string[]
}
export interface KnowledgeRestructurePlan {
  verb: string; item_id: string; summary: string; token: string
  affected: string[]; breaks: KnowledgeRestructureBreak[]
  relink_offered: boolean
  detail: Record<string, unknown>
}
export interface KnowledgeRestructurePreview {
  confirmed: false; token: string; plan: KnowledgeRestructurePlan
}
export interface KnowledgeRestructureResult {
  ok: boolean; confirmed: true; kept: string; created: string[]
  undo_token: string; summary: string
  idempotent: boolean
  annotations_moved?: number; citations_widened?: number
  moved?: KnowledgeMergeResult['moved']
  wikilinks_relinked?: { items: number; links: number }
  logical_key?: string; title?: string; kind?: string
}
export interface KnowledgeUndoEntry {
  token: string; verb: string; item_id: string; summary: string; created_at: string
}
export interface ChatFolder { id: string; name: string; order?: number; collapsed?: boolean; parent_id?: string }
export interface ChatTag { id: string; name: string; color?: string; order?: number; status?: boolean }
export interface OrganizeProposal {
  session: string; folder_id: string; folder_name: string; tags: string[]
  source: 'title' | 'workspace' | 'channel' | 'llm' | string; reason: string; dedup_key?: string
}
export interface RetagJob { id?: string; status: 'idle' | 'running' | 'done' | 'error' | 'cancelled'; done?: number; total?: number; updated?: number; skipped?: number; errors?: number; current?: string; error?: string }
export interface TagColumn { id: string; name?: string; tag_ids?: string[]; mode?: 'any' | 'all' | 'none'; order?: number; include_untagged?: boolean }
export interface ChatHistoryMsg {
  role: string; content: string; ts?: string; cls?: string
  meta?: { tool_call_id?: string; input?: string; purpose?: string; output?: string; done?: boolean; tool?: string; memory_citations?: { n: number; id: string | null; preview?: string }[]; skills_used?: { name: string; state: string; loaded_tokens: number }[] }
}

export interface NotificationItem {
  kind: string; title: string; body: string; ts: string
  job_id?: string; loop_id?: string; loop_kind?: string; acked: boolean
  reversal_id?: string; reversal?: string; action_type?: string; rung?: string
}
export type ScheduleKind = 'every' | 'cron' | 'at'
export type ScheduleExecMode = 'agent' | 'script' | 'command' | 'other'
export interface ScheduleJob {
  id: string; name: string; message: string; enabled: boolean
  author?: string; read_only?: boolean
  schedule: string
  cron_expr?: string | null
  every_secs?: number | null
  created_ts?: number | null
  last_status?: string | null
  last_run_status?: string | null
  agent?: string | null; model?: string | null
  channel?: string | null; approval_mode?: string | null
  silent?: boolean; strict_schedule?: boolean; timezone?: string | null
  skip_dates?: string[]
  script?: string | null; command?: string | null
  action?: { provider?: string; config?: Record<string, unknown> }
  last_run_ts?: number | null; next_run_ts?: number | null
  has_result?: boolean; last_result?: string | null; last_error?: string | null
  is_running?: boolean; running_since?: number | null; has_session?: boolean
  broken?: string[]
  warnings?: string[]
}
export interface ScheduleRun {
  id?: string
  run_id?: string; job_id?: string; job_name?: string
  trigger?: string

  started_at?: number | string; finished_at?: number | string; duration_ms?: number
  status?: string
  summary?: string; error?: string; trace?: string
  outcome?: string
  reason?: string
  weight?: string
  incomplete?: boolean
}
export interface PartitionedRunHistory<T> {
  visible: T[]
  suppressed: T[]
}
export function partitionRunHistory<T>(
  rows: readonly T[],
  statusOf: (row: T) => string | null | undefined,
): PartitionedRunHistory<T> {
  const visible: T[] = []
  const suppressed: T[] = []
  for (const row of rows) {
    const status = statusOf(row) ?? ''
    if (status === 'skipped' || status.startsWith('skipped_')) suppressed.push(row)
    else visible.push(row)
  }
  return { visible, suppressed }
}
export type TaskStatus = 'open' | 'in_progress' | 'blocked' | 'done' | 'cancelled' | 'skipped'
export type TaskPriority = 'critical' | 'high' | 'medium' | 'low' | 'trivial'
export type DependencyType = 'BLOCKS' | 'REQUIRED_FOR'
export interface TaskDependency { task_id?: string; depends_on_task_id?: string; dependency_type?: DependencyType }
export interface ExitCriterion { description: string; status?: 'incomplete' | 'complete'; comment?: string; met?: boolean }
export interface ActionPlanItem { content?: string; description?: string; sequence?: number; completed?: boolean }
export interface TaskNote { content: string; timestamp?: string; created_at?: string; phase?: 'research' | 'execution' | 'general' }
export interface ProjectItem { id: string; name: string; is_builtin?: boolean; status?: 'active' | 'archived'; workspace_dir?: string; context_dir?: string; name_locked?: boolean; agent_instructions_template?: string; brief?: string; task_list_count?: number; created_at?: string; updated_at?: string }
export interface ProjectLinkedItem { id: string; name: string; status: string; error_message?: string | null }
export type SharingPolicy = 'private' | 'shared'
export interface ProjectKnowledgeItem {
  id: string
  title: string
  kind: string
  summary: string
  updated_at: string
  project_id: string
  run_id: string
  sharing_policy: SharingPolicy
  source_project: string
}
export type WorkState = 'needs_input' | 'working' | 'queued' | 'suspended' | 'review' | 'done'
export interface WorkClaim { holder: string; expires_at: number; taken_at: number; renewals: number }
export interface WorkRow {
  run_id: string; title: string; state: WorkState; origin: string; project_id: string
  claim: WorkClaim | null; collapsed: boolean; attention: boolean; resumable: boolean
}
export interface WorkGroup { state: WorkState; count: number; attention: number; rows: WorkRow[] }
export interface WorkSection { name: string; items: WorkRow[]; status: 'ok' | 'loading' | 'error'; error: string; loadedAt: number }
export interface WorkBoard {
  board: WorkGroup[]; sections: WorkSection[]
  completeness: 'complete' | 'inferred' | 'partial' | 'error'
  attention: number; loadedAt: number
}
export interface TaskListItem { id: string; name: string; project_id: string; agent_instructions_template?: string; created_at?: string; updated_at?: string }
export interface BlockReason { is_blocked?: boolean; blocking_task_ids?: string[]; blocking_task_titles?: string[]; message?: string }
export interface TaskItem {
  id: string; title: string; status: string; description?: string
  provider?: string; project?: string; assignee?: string; priority?: string
  author?: string
  labels?: string[]; depends_on?: string[]; due?: string; url?: string
  created_at?: string; updated_at?: string
  task_list?: string
  dependencies?: TaskDependency[]
  exit_criteria?: ExitCriterion[]
  action_plan?: ActionPlanItem[]
  notes?: TaskNote[]
  research_notes?: TaskNote[]
  execution_notes?: TaskNote[]
  agent_instructions_template?: string
  block_reason?: BlockReason
  blocked_reason_kind?: string
  task_list_id?: string
  order?: number
  comment_count?: number
  reconciled?: TaskItem[]
}
export interface TaskGraphEdge { from: string; to: string; type: DependencyType }
export interface DependencyAnalysis {
  completion_pct: number; leaf_task_ids: string[]; root_task_ids: string[]
  critical_path: string[]; cycles: string[][]
  bottleneck_tasks?: { id: string; dependents: number }[]
}
export interface TaskGraphData { tasks: TaskItem[]; edges: TaskGraphEdge[]; analysis: DependencyAnalysis }
export interface TaskComment { id: string; task_id: string; author: string; body: string; created_at: string }

export interface ApiProposedTask { title: string; description?: string; priority?: string; depends_on?: number[] }

export interface WorkflowDefStub {
  id: string; name: string; description?: string; enabled?: boolean
  scope?: string; tags?: string[]; steps?: Array<{ id?: string; title: string; instruction?: string }>
}

export interface WorkflowNode {
  kind: string; id?: string
  children?: WorkflowNode[]
  body?: WorkflowNode
  cases?: Record<string, WorkflowNode>
  default?: WorkflowNode
  config?: Record<string, unknown>
  needs?: string[]
}
export interface WorkflowDefSummary {
  name: string; description: string; source: string; version: number; tags: string[]; provider: string
}
export interface WorkflowSurfacingRow {
  name: string; provider: string
  surface_mode: 'off' | 'passive' | 'suggest'
  summary: string; when_to_use: string
  cadence_days: number
  escalation: 'manual' | 'auto'
  packs: string[]
  guided: boolean
  freshness: 'never_run' | 'fresh' | 'due_soon' | 'overdue' | 'stale'
  overdue: boolean
  last_completed_at: number
  hands_off_to: Array<{ target_def: string; condition: string; context_fields: string[]; requires_user_request: boolean }>
}
export interface WorkflowSurfacingFinding { name: string; code: string; detail: string }
export interface WorkflowInputParam {
  type?: string; required?: boolean; default?: unknown; help?: string
}
export interface WorkflowDef {
  name: string; description?: string; version?: number; source?: string; provenance?: string
  root: WorkflowNode
  inputs?: Record<string, WorkflowInputParam>
  tags?: string[]
  metadata?: {
    risk?: string
    requirements?: Record<string, string[]>
    steering_examples?: Array<{ event?: string; description?: string }>
    hands_off_to?: WorkflowHandoff[]
    a2a_published?: boolean
  }
}
export interface WorkflowHandoff {
  target_def: string
  condition?: string
  context_fields?: string[]
  requires_user_request?: boolean
}
export interface WorkflowVersionRow {
  version: number
  source: string
  created_at: string
  note: string
  run_ids: string[]
  ops_count: number
}
export interface WorkflowVersionOp {
  op: string
  node_id?: string
  kind?: string
  fields?: string[]
}
export interface WorkflowMaturity {
  level: number
  label: string
  signals: Record<string, boolean>
  clean_runs: number
  evaluator_rejected: boolean
}
export interface WorkflowLedgerRow {
  run_id: string
  status: string
  spec_version: number
  created_at?: string
  totals: {
    tokens?: number
    cost_usd?: number
    priced?: boolean
    steps_completed?: number
    steps_failed?: number
  }
}
export type WorkflowRunStatus =
  'draft' | 'running' | 'paused' | 'needs_input' | 'complete' | 'failed' | 'cancelled' | 'escalated'
export interface WorkflowNodeState {
  instance_path: string; node_id: string; state: string; attempt?: number
  cached?: boolean
  degraded_reason?: string
  failure?: { class?: string; cause_plain?: string; remediation?: string; terminal_reason?: string } | null
  item_index?: number; item_total?: number; item_label?: string
}
export interface WorkflowRunSummary {
  id: string; workflow_name: string; status: WorkflowRunStatus; spec_version: number
  created_at: string; started_at?: string | null; completed_at?: string | null
  elapsed_seconds?: number; total_tokens?: number; error_message?: string
  attention?: Record<string, unknown> | null
  project_id?: string; mode?: string
}
export interface WorkflowRunDetailData {
  run_id: string; workflow: string; status: WorkflowRunStatus; spec_version: number
  error?: string; attention?: Record<string, unknown> | null
  tokens?: number; elapsed_secs?: number
  project_id?: string
  policy_overrides?: Record<string, unknown>
  nodes: WorkflowNodeState[]
}
export interface WorkflowContinuation {
  resume_token: string; node_id: string; instance_path: string
  ask: { kind?: string; prompt?: string; choices?: string[]; fields?: Array<{ name: string; type?: string; label?: string; required?: boolean; choices?: string[] }> }
  handoff: { scope?: string; status?: string; outstanding?: string[]; checks_run?: string[]; next_steps?: string[]; risks?: string[] }
  expires_at: number; expired: boolean
}
export interface WorkflowCascadePreview {
  rerun: string[]; stale: string[]; skipped: string[]; committed_effects: string[]; needs_confirmation: boolean
}
export interface ReviewFinding {
  key: string; severity: string; location: string; problem: string; why: string
  recommended_fix: string; status: string; auto_fixable: boolean; line_text: string
  origin_run_id: string; origin_node_id: string; origin_session_key: string
  anchor_state: 'anchored' | 'unanchored'; anchor_reason: string
  resolved_path: string; resolved_line: number; diff_line_text: string
}
export interface WorkflowReviewPayload {
  run_id: string; workspace: string; diff: string; diff_truncated: boolean
  findings: ReviewFinding[]
  counts: { total: number; anchored: number; unanchored: number }
  terminal: boolean
}
export interface WorkflowTriageResult {
  run_id: string; dry_run: boolean; brief?: string
  accepted: ReviewFinding[]
  rejected: Array<ReviewFinding & { rejection_reason: string }>
  refused: Array<ReviewFinding & { refused_reason: string }>
  untriaged: ReviewFinding[]
  receipt: { delivered: boolean; reason: string; target: string; brief: string; count: number }
  calibrated?: number
  auto_apply_candidates?: string[]
}
export interface NodeInspect {
  run_id: string; node_id: string; instance_path: string; state: string
  resolved_prompt: string | { ref: string }
  resolved_inputs: Record<string, unknown>
  output: unknown | { artifact_ref: string }
  attempts: Array<Record<string, unknown>>
  ledger_events: Array<Record<string, unknown>>
  cached: boolean
}
export interface WorkflowDiffEntry { path: string; status: string; staged: boolean }
export interface WorkflowReintegrationVerb {
  verb: 'apply_locally' | 'checkout_branch'; label: string; detail: string; safe: boolean
}
export interface WorkflowWorkspaceReview {
  run_id: string
  workspace: {
    run_id: string; path: string; branch: string; alive: boolean; dirty: boolean
    changed: WorkflowDiffEntry[]
    preserved_workspace_path: string
  }
  reintegration: {
    run_id: string; branch: string; changed_files: number; conflicts: string[]
    verbs: WorkflowReintegrationVerb[]
    note: string
  }
  declared: {
    mode?: string; isolated?: boolean; name?: string; degraded_reason?: string
    setup?: { ran: string[]; skipped: string[]; failed: string[]; blocked_run: boolean }
    issues?: Array<{ code: string; message: string; fatal: boolean }>
  }
  preview?: {
    ports: Array<{ port: number; url: string; pid: number; command: string; address: string }>
    root: string
    scanned: boolean
    reason: string
  }
}
export interface WorkflowOutboxEntry {
  slug: string
  artifact: string
  kind: string
  action: string
  change_note: string
  node_id: string
  updated_at: string
  self_contained: boolean
}
export interface WorkflowRunStats {
  run_id: string
  tokens: number | null
  tokens_recorded: boolean
  cached_tokens: number
  cost_usd: number
  priced: boolean
  steps_completed: number
  steps_failed: number
  steps_cached: number
  duration_secs: number
  first_byte_ms: number
  models: string[]
  unverified_steps: number
  verification_debt: number
  cache_hit_rate: number
}
export interface WorkflowGateStats {
  node_id: string
  passes: number
  rejects: number
  retries_consumed: number
  total: number
  pass_rate: number
  fake_check_warning: string
}
export interface WorkflowBranchStats {
  path: string
  cases: Record<string, number>
  routed_runs: number
  never_taken: string[]
  degenerate_warning: string
}
export interface WorkflowJudgeStats {
  node_id: string
  verdicts: Record<string, number>
  total: number
  degenerate_warning: string
}
export interface WorkflowEdgeStats {
  branches: Record<string, WorkflowBranchStats>
  judges: Record<string, WorkflowJudgeStats>
}
export interface WorkflowTemplateCard {
  template: string
  runs: number
  cost_p50: number
  cost_p95: number
  priced: boolean
  duration_p50: number
  duration_p95: number
  failure_rate: number
  warnings: string[]
}
export interface WorkflowProofSection {
  summary: string
  verified_steps: number
  total_steps: number
  coverage: number
  evidence_files: string[]
  warnings: string[]
  honest: boolean
}
export interface WorkflowTimelineRow {
  kind: string
  ts: string
  node_id: string
  instance_path: string
  attempt?: number | null
  state: string
  duration_secs?: number | null
  tokens?: number | null
  cost_usd?: number | null
  model: string
  approved?: boolean | null
  detail: string
}
export interface WorkflowNextIfSilent {
  action: 'nothing' | 'waits' | 'proceeds'
  detail: string
  queued: string[]
}
export interface WorkflowIntrospection {
  run_id: string
  workflow: string
  stats: WorkflowRunStats
  gates: Record<string, WorkflowGateStats>
  edges: WorkflowEdgeStats
  template_card: WorkflowTemplateCard
  proof: WorkflowProofSection
  timeline: WorkflowTimelineRow[]
  touched: WorkflowTouchedItem[]
  answers: {
    running: { status: string; workflow: string; nodes: unknown[] }
    changed: WorkflowTimelineRow[]
    blocked: unknown[]
    approval: Array<{ resume_token: string; node_id: string; ask: unknown }>
    failed: unknown[]
    cost: WorkflowRunStats
    risky: { degraded: unknown[]; gates: WorkflowGateStats[]; edges: WorkflowEdgeStats; verification_debt: number }
    next: WorkflowNextIfSilent
    proof: WorkflowProofSection
  }
  checklist_gaps: string[]
}
export interface WorkflowFindingRow {
  ts: string
  node_id: string | null
  instance_path: string | null
  epoch: number | null
  state: string | null
  model: string | null
  provider: string | null
  tokens: number | null
  cost_usd: number | null
  duration_secs: number | null
  retries: number | null
  degraded_reason: string | null
  output_ref: string | null
  cycle: number | null
}
export interface WorkflowVerdictRow {
  ts: string
  node_id: string | null
  instance_path: string | null
  epoch: number | null
  template: string | null
  verdict: string | null
  status: string | null
  overall: number | null
  sample_count: number | null
  shortfalls: string[] | null
  marginal_value: number | null
  quality_score: number | null
}
export interface WorkflowRailCoverage {
  kind: string
  producer: 'engine' | 'none'
  events: number | null
}
export interface WorkflowLedgerRails {
  run_id: string
  workflow: string
  findings: WorkflowFindingRow[]
  verdicts: WorkflowVerdictRow[]
  totals: {
    steps_completed: number
    verdicts: number
    cost_usd: number | null
    tokens: number | null
    duration_secs: number | null
    verdicts_by_word: Record<string, number>
    overall_series: number[] | null
    absent_scores: string[]
  }
  coverage: WorkflowRailCoverage[]
}
export type WorkflowDeliverableAbsence =
  | 'template_unknown'
  | 'kind_has_no_document'
  | 'not_written'
  | 'no_root'
  | 'unreadable'
export interface WorkflowDeliverableDoc {
  name: string | null
  present: boolean
  content: string | null
  bytes: number | null
  modified_at: number | null
  truncated: boolean
  clipped_blobs: number
  found_in: 'workspace' | 'run_dir' | null
  absent_reason: WorkflowDeliverableAbsence | null
}
export interface WorkflowRunDeliverable {
  run_id: string
  workflow: string
  report: WorkflowDeliverableDoc
  log: WorkflowDeliverableDoc
  derivation: {
    name: string | null
    reason: WorkflowDeliverableAbsence | null
    declared_by: { kind: string; variant: string; name: string } | null
  }
  roots: Array<{ kind: 'workspace' | 'run_dir'; path: string; exists: boolean }>
  instructed: boolean | null
}
export interface PinnedArtifact {
  slug: string
  pinned_at: string
  run_id: string
}
export interface WorkflowTouchedItem {
  kind: 'artifact' | 'file'
  ref: string
  label: string
  action: string
  detail: string
  node_id: string
  ts: string
}
export interface WorkflowDroppedFile {
  filename: string
  size: number
  sha256: string
  mime?: string
  lifecycle?: string
  accepted_at?: string
  approved?: boolean
}
export interface WorkflowDropStatus {
  enabled: boolean
  reason: string
  auto_accept_mimes: string[]
  max_files: number
  files: WorkflowDroppedFile[]
  accepted?: WorkflowDroppedFile[]
}
export interface WorkflowManifest {
  spec_semver: string
  node_kinds: Array<{ kind: string; container: boolean; lane: string }>
  gate_kinds: string[]; join_modes: string[]; loop_modes: string[]; item_error_policies: string[]
  pipes: string[]; mutation_ops: string[]; instance_states: string[]; run_statuses: string[]
}
export type PromptVarType = 'text' | 'textarea' | 'number' | 'boolean' | 'select'
export type PromptKind = 'system' | 'user'
export type PromptSource = 'user' | 'bundled' | 'marketplace'
export interface PromptVariable { name: string; type: PromptVarType; description?: string; required?: boolean; default?: unknown; options?: string[] }
export interface LaunchSpec {
  kind?: LoopKind; agent?: string; model?: string; provider?: string; provider_agent?: string
  reasoning_effort?: string; execution?: 'solo' | 'multi_agent'; roster?: RosterMember[]
  strategy_id?: string; intake_rigor?: string; attended?: boolean; autopilot?: boolean
  max_cycles?: number; max_cost_usd?: number; deadline_secs?: number
  skill_ids?: string[]; workflow_ids?: string[]; project_id?: string
  success_criteria?: string; kind_config?: Record<string, unknown>
}
export interface PromptItem {
  name: string; kind?: PromptKind; title?: string; description?: string; content?: string
  variables?: PromptVariable[]; tags?: string[]; source?: string; updated_at?: number
  launch_spec?: LaunchSpec
  merged_variables?: PromptVariable[]; includes?: string[]
}
export interface PromptSnippet {
  name: string; title?: string; description?: string; content?: string
  variables?: PromptVariable[]; tags?: string[]; source?: string; updated_at?: number
  used_by?: { prompts: string[]; snippets: string[] }
}
export interface PromptBinding {
  use_case: string; ref: string; effective_ref: string
  label: string; hint: string; category: string
}
export interface PromptCategoryGroup { key: string; label: string; hint: string }
export interface PromptBindings {
  use_cases: string[]; default_ref: string; bindings: PromptBinding[]
  categories: PromptCategoryGroup[]; available: PromptItem[]
}
export interface PromptPreview { ok: boolean; rendered?: string; error?: string; detected_variables: PromptVariable[]; includes: string[] }
export interface PromptSyntaxFn { name: string; category: string; signature: string; description: string; insert: string }
export interface PromptSyntaxConstruct { category: string; label: string; snippet: string; description: string }
export interface PromptSyntax { functions: PromptSyntaxFn[]; constructs: PromptSyntaxConstruct[] }
export interface SkillItem { key: string; name: string; description: string; always: boolean; path?: string; source: string; type: string; provenance?: 'auto' | 'taught' | ''; loaded_by_agents: string[]; integrity?: 'intact' | 'tampered' | 'unverified'; agent?: string }
export interface EphemeralDraft { slug: string; title: string; body: string; created_at: string }
export interface SkillProposal { id: string; slug: string; description: string; triggers: string; kind: string; refine_target?: string; trigger?: string; session_key: string; created_at: string; status: string; procedure_preview: string }
export interface SkillLadderReview { verdict: string; elapsed_ms: number; session_key: string; detail: string; at: string }
export interface SkillProposalFeed { proposals: SkillProposal[]; lastReview: SkillLadderReview | null }
export interface SkillProposalDetail extends SkillProposal { procedure_md: string; source_excerpt: string; diff?: string; version?: number }
export interface LearningSummaryGroup { count: number; names: string[] }
export interface LearningSummary {
  window_days: number
  total: number
  new_skills: LearningSummaryGroup
  refined_skills: LearningSummaryGroup
  pending_proposals: LearningSummaryGroup
  facts: LearningSummaryGroup
}
export interface SkillIntegrity { name: string; integrity: 'intact' | 'tampered' | 'unverified'; ok: boolean; unlocked: boolean; mutated: string[]; missing: string[]; added: string[]; summary: string }
export interface SkillFile { path: string; size: number }
export interface SkillMarketplace { name: string; type: string }
export interface SkillSearchResult { id: string; name: string; description: string; source: string; url?: string; installs?: number }
export interface SkillMarketplaceDetail { id: string; name: string; audit_status?: string; files: Array<{ path: string; binary?: boolean }>; frontmatter?: Record<string, unknown>; body?: string; marketplace?: string }
export interface ToolItem { name: string; description: string; provider: string; parameters?: Record<string, unknown>; requires_approval?: boolean; risk_level?: 'safe' | 'caution' | 'destructive'; disabled: boolean; locked?: boolean; providerDisabled: boolean; group?: string; tier?: string }
export interface ToolLoadFailure { provider: string; error: string }
export interface ManifestToolExample { summary: string; args: Record<string, unknown> }
export interface ManifestTool { name: string; provider: string; description: string; parameters?: Record<string, unknown>; requires_approval: boolean; risk_level: string; response_type: string; error_codes: string[]; examples: ManifestToolExample[] }
export interface ManifestRoute { method: string; path: string; summary: string; agent_callable: boolean }
export interface ManifestProvider { app: string; type: string; provider_type: string; capabilities: string[]; enabled: boolean; error?: string | null }
export interface Manifest { apiVersion: number; tools: ManifestTool[]; routes: ManifestRoute[]; app_surfaces: unknown[]; providers: { types: string[]; registered: ManifestProvider[] } }
export interface DiscoverTryIt { route: string; query: Record<string, string>; label: string }
export interface DiscoverTip { id: string; area: string; title: string; lesson: string; try_it: DiscoverTryIt }
export interface DiscoverArea { area: string; tips: DiscoverTip[] }
export interface DiscoverResponse { enabled: boolean; areas: DiscoverArea[]; visible_count: number; total: number; dismissed_count?: number; restorable_count?: number; engaged_count?: number }
export interface AlwaysOnItem {
  id: string; kind: 'always_skill' | 'project_instruction'; name: string
  scope: 'global' | 'project'; source: string; path: string; chars: number
  editable: boolean; read_only_reason: string; project_id: string; preview: string
  body?: string
}
export interface AlwaysOnResponse {
  items: AlwaysOnItem[]; project_id: string
  counts: { total: number; always_skills: number; project_instructions: number }
  always_skill_mechanism: string
}
export interface McpServer {
  name: string; command?: string; args?: string[]; status: string; tools: Array<string | { name: string; description?: string }>
  error?: string; source?: string; enabled?: boolean; presence?: Record<string, boolean>
}
export interface McpPoolStats {
  available: boolean
  live_connections?: number; shared_conns?: number; session_conns?: number
  configured_servers?: number; spawns?: number; reaps?: number; served?: number
  evicted?: number; reused?: number
}
export interface ImportableMcpServer {
  name: string; backend: string; command?: string; args?: string[]
  env?: Record<string, string>; url?: string; headers?: Record<string, string>
}
export interface ToolInvokeResult { ok: boolean; output?: string; error?: string }
export type HookEnforcement = 'enforcing' | 'not_enforcing' | 'advisory'
export interface HookItem {
  id: string; name: string; event: string; matcher: string; provider: string; provider_config: Record<string, unknown>
  timeout: number; enabled: boolean; last_run: number; last_status: string; run_count: number; used_by: string[]
  blocking?: boolean; enforcement?: HookEnforcement
}
export type EventPattern =
  | 'MemoryUpdate' | 'MemoryKeyPattern' | 'ContentMatch'
  | 'InboxMessage' | 'InboxSender' | 'InboxAddress'
  | 'AppEvent'
export interface TriggerAction { provider: string; config: Record<string, unknown> }
export function isOutcomeRoute(value: string): boolean {
  const route = value.trim()
  return route === '' || route === 'none' || route === 'inbox' || route === 'notify'
    || (route.startsWith('channel:') && route.slice('channel:'.length).length > 0 && !/\s/.test(route))
}
export interface Trigger {
  kind: 'schedule' | 'lifecycle' | 'event' | 'store'; id: string; raw_id: string; name: string; enabled: boolean
  action: TriggerAction
  delivery?: string; failure_delivery?: string; failure_policy?: Record<string, unknown>
  pattern?: string; sender_glob?: string; address_glob?: string; key_glob?: string; content_re?: string
  event_glob?: string; fire_count?: number; max_fires?: number
  store_kind?: string; created_by?: string; spec?: Record<string, unknown>
  health?: string; state?: string; broken?: string[]; warnings?: string[]
  author?: string; read_only?: boolean
  message?: string; schedule?: string; cron_expr?: string | null; every_secs?: number | null
  agent?: string | null; model?: string | null; channel?: string | null; approval_mode?: string | null
  silent?: boolean; strict_schedule?: boolean; timezone?: string | null; skip_dates?: string[]
  script?: string | null; command?: string | null
  last_run_ts?: number | null; next_run_ts?: number | null; last_status?: string | null; last_run_status?: string | null
  has_result?: boolean; last_result?: string | null; last_error?: string | null
  is_running?: boolean; running_since?: number | null; has_session?: boolean; created_ts?: number | null
  event?: string; matcher?: string; timeout?: number; last_run?: number; run_count?: number; used_by?: string[]
  last_fired_at?: number | null
  blocking?: boolean; enforcement?: HookEnforcement
}
function _scheduleBodyToWire(body: Record<string, unknown>): Record<string, unknown> {
  const { message, agent, model, approval_mode, script, command, zt_timeout, action, ...rest } = body
  if (action) return { ...rest, action }
  let act: TriggerAction
  if (script) act = { provider: 'run-script', config: { script, timeout: Number(zt_timeout) || 0 } }
  else if (command) act = { provider: 'bash', config: { command, timeout: Number(zt_timeout) || 0 } }
  else if (!('message' in body)) return rest
  else act = { provider: 'invoke-agent', config: { task_template: message ?? '', agent: agent ?? '', model: model ?? '', approval_mode: approval_mode ?? '' } }
  return { ...rest, action: act }
}

function _triggerToHook(t: Trigger): HookItem {
  return {
    id: t.raw_id, name: t.name, event: t.event ?? '', matcher: t.matcher ?? '',
    provider: t.action.provider, provider_config: t.action.config ?? {},
    timeout: t.timeout ?? 30, enabled: t.enabled, last_run: t.last_run ?? 0,
    last_status: t.last_status ?? '', run_count: t.run_count ?? 0, used_by: t.used_by ?? [],
    blocking: t.blocking, enforcement: t.enforcement,
  }
}
export interface SchemaMeta {
  label?: string
  help?: string
  widget?: string
  sensitive?: boolean
  placeholder?: string
  tags?: string[]
}
export interface JsonSchema {
  type?: string | string[]
  description?: string
  minimum?: number
  maximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  properties?: Record<string, SchemaProp>
  required?: string[]
  items?: JsonSchema
  enum?: unknown[]
  default?: unknown
}
export interface SchemaProp extends JsonSchema {
  'x-meta'?: SchemaMeta
}
export interface ActionProvider {
  name: string; display_name: string; supports_blocking: boolean
  settingsSchema: JsonSchema
}
export interface LifecycleEventInfo { event: string; label: string; desc: string; vars: string[]; blocking: boolean; dormant?: boolean; dormant_reason?: string; agent_scoped?: boolean }
export interface AppSourceEvent { event: string; source_event: string }
export interface AppSourceInfo { app: string; label: string; events: AppSourceEvent[] }
export interface TriggerVariables { schedule: string[]; lifecycle: LifecycleEventInfo[]; app_sources: AppSourceInfo[] }
export interface TriggerRunResult { ok: boolean; name?: string; result?: unknown; refused?: string; running?: boolean }
export interface LearningRow {
  id: string; kind: string; title: string; provenance: string
  source_cadence: string; source_excerpt: string
  evidence_refs: string[]; evidence_strength: string; reinforcements: number; confidence: number
  manifest_valid: boolean; manifest_issues: string[]
  risk_tier: string; status: string
  renderable: boolean; bulk_acceptable: boolean
  gate: LearningGate
  replay: LearningReplay
}
export interface LearningReplay {
  state: 'replayed' | 'unreplayed'
  reason: string
  verdict: 'improved' | 'neutral' | 'regressed' | 'unmeasured'
  candidate_mean: number | null; baseline_mean: number | null
  cases: number; scored: number; rejected: number; tool_free: number
  deferred: boolean
  provenance: string[]
  ran_at: string
}
export interface LearningGate {
  state: 'gated' | 'ungated'
  reason: string
  before: number | null; after: number | null; delta: number | null
  regressed: boolean
  scenarios: number
  halted: boolean
  dollars_est: number; spend_observed: boolean
  pin: { model_fp?: string; scenario_sha256?: string }
  ran_at: string
}
export interface LearningInbox {
  rows: LearningRow[]; total: number
  by_kind: Record<string, number>; by_tier: Record<string, number>
  flagged: number; unrenderable: string[]; bulk_acceptable: number
}
export interface StagingDay {
  day: string; passes: number; by_outcome: Record<string, number>
  produced: number; errors: number; staged: number
  cost_usd: number; proposal_ids: string[]
}
export interface StagingWeek {
  days: number; buckets: StagingDay[]
  silent_days: string[]; error_days: string[]
  produced_total: number; cost_usd: number
  first_pass_day?: string | null
}

export type AttributionVerdict =
  | 'EFFECTIVE' | 'PARTIALLY_EFFECTIVE' | 'INEFFECTIVE' | 'MIXED' | 'HARMFUL' | 'PENDING'

export interface HealthComponent {
  name: 'precision' | 'capture' | 'utilization' | 'judge'
  score: number | null
  weight: number
  detail: string
}
export interface MaeBucket {
  bucket: string
  n: number
  labelled: number
  mae: number | null
}
export interface JudgeBenchRow {
  rubric_class: string
  tier: string
  samples: number
  agreement: number | null
  scored_cells: number
  verifier_absent: number
  protocol_errors: number
  separation: number | null
  flip_rate: number | null
  swapped_fixtures: number
  false_passes: number
  false_rejects: number
  forbidden_missed: number
  cost_usd: number | null
  wall_secs: number
  calls: number
  adequate: boolean
  inadequate_reasons: string[]
  notes: string[]
}
export interface JudgeBenchRecommendation {
  rubric_class: string
  verdict: string
  tier: string
  samples: number
  use_case: string
  model_ref: string
  cost_usd: number | null
  notes: string[]
}
export interface AttentionScope {
  scope: string
  runs: number
  attention_events: number
  events_per_run: number
  dwell_p50_secs: number
  debt: number
  trend: '' | 'rising' | 'falling' | 'flat'
}

export interface JudgeBenchView {
  bench_id: string
  columns: string[]
  rows: JudgeBenchRow[]
  floors: { agreement?: number; separation?: number; flip_rate?: number }
  recommendations: JudgeBenchRecommendation[]
  pin: Record<string, unknown> | null
  runs: string[]
}

export interface FieldMetricsField {
  ups: number
  downs: number
  thumb_rate: number | null
  edited_runs: number
  clean_approved_runs: number
  edit_before_approve_rate: number | null
  approvals: number
  rejections: number
  undos: number
  approval_rate: number | null
  signals: number
  trend: '' | 'rising' | 'falling' | 'flat'
}
export interface FieldMetricsRow {
  subject_kind: 'template' | 'action_type'
  subject: string
  lab: {
    score: number | null
    previous: number | null
    rose: boolean | null
    verdict: string
    study_id: string
    model_fp: string
    ts: number | null
  } | null
  gate: LearningGate | null
  field: FieldMetricsField
  lab_field_divergence: boolean
  divergence_reason: string
}
export interface AblationArmAggregate {
  counts: Record<string, number>
  total: number
  scored_count: number
  mean_score: number | null
}
export interface EvaluationArmExecution {
  arm: string
  executed: boolean
  cells: number
  scored_cells: number
  verifier_absent: number
}
export interface AblationReportView {
  component_id: string
  kind: string
  target: string
  subject: string
  verdict: string
  arms: Record<string, AblationArmAggregate>
  delta: number | null
  cheap_delta: number | null
  epsilon: number
  matrix_id: string
  trials: number
  created_at: string
  live_state: Record<string, string>
}
export function evaluationArmExecutions(
  report: Pick<AblationReportView, 'arms'>,
): EvaluationArmExecution[] {
  return Object.entries(report.arms).map(([arm, aggregate]) => ({
    arm,
    executed: arm.length > 0 && aggregate.total > 0,
    cells: aggregate.total,
    scored_cells: aggregate.scored_count,
    verifier_absent: aggregate.counts.verifier_absent ?? 0,
  }))
}
export interface AblationRegistryRow {
  component_id: string
  kind: string
  target: string
  subject: string
  off_value: unknown
  cheap_value: unknown
  live_refs: string[]
  description: string
}
export interface AblationHistoryEntry {
  ts: string
  component_id: string
  verdict: string
  matrix_id: string
  delta: number | null
  proposal: string
}
export interface AblationView {
  report: AblationReportView
  verdict_vocabulary: string[]
  registry: AblationRegistryRow[]
  history: AblationHistoryEntry[]
  last_run_ts: string
  cadence_days: number
  due: boolean
}
export interface BenchmarkArmAggregate {
  trials: number
  mean_score: number
  spread: number
  tokens: number
  tokens_per_point: number
}
export interface BenchmarkTaskRow {
  task_id: string
  skill: string
  verdict: string | null
  verdict_class: string | null
  reason: string
  delta_points: number | null
  token_ratio: number | null
  arms: Record<string, BenchmarkArmAggregate>
  absent_cells: number
  tool_calls: Record<string, number>
  spend_observed: boolean
  spend_estimated: boolean
  tokens_recorded?: boolean
  unrecorded_spend_cells?: number
  notes: string[]
}
export interface BenchmarkSkippedRow {
  task_id: string
  skill: string
  blockers: string[]
}
export interface BenchmarkProviderBinding {
  use_case: string
  provider_name: string
  model: string
  protocol: string
  base_url: string
  api_key_env: string
  max_tokens: number | null
}
export interface BenchmarkPin {
  prompt_pack_sha256?: string
  config_snapshot_ref?: string
  model_fp?: string
  model_fingerprint?: Record<string, string>
  cell_model_fp?: string
  cell_model_fingerprint?: Record<string, string> | null
}
export interface BenchmarkReport {
  run_id: string
  report_schema?: number
  created_at: string
  protocol_doc: string
  task_set_version: number
  task_set_fingerprint: Record<string, string>
  trials_per_arm: number
  arms: string[]
  thresholds: {
    inconclusive_band_points: number
    token_match_tolerance: number
    min_trials_per_arm: number
    source: string
  }
  tasks: BenchmarkTaskRow[]
  skipped: BenchmarkSkippedRow[]
  measured_tasks: number
  absent_cells: number
  reproduction?: BenchmarkReproduction
  pin?: BenchmarkPin
  provider_binding?: BenchmarkProviderBinding | null
  tokens_recorded?: boolean
  unrecorded_spend_cells?: number
}
export interface BenchmarkReproduction {
  baseline_run_id: string
  rerun_run_id: string
  reproduces: boolean
  stated_variance: string[]
  stated_variance_source: string
  conditions: Record<string, boolean>
  verdict_changes: { task_id: string; baseline: string | null; rerun: string | null }[]
  notes: string[]
  baseline_report_schema?: number | null
  rerun_report_schema?: number | null
}
export interface BenchmarkView {
  report: BenchmarkReport
  register: { task_id: string; skill: string; observable: string }[]
  task_set_version: number
  protocol_doc: string
  stated_variance: string[]
}
export interface RetrievalMaskRow {
  mask: string
  k: number
  p_at_k: number | null
  r_at_k: number | null
  queries: number
  scored_queries: number
  no_candidate_queries: number
  undefined_recall_queries: number
}
export interface RetrievalArmContribution {
  arm: string
  full_p_at_k: number | null
  without_p_at_k: number | null
  contribution_p: number | null
  full_r_at_k: number | null
  without_r_at_k: number | null
  contribution_r: number | null
  solo_p_at_k: number | null
  scored_queries: number
  verdict: string
  reasons: string[]
}
export interface RetrievalStoreReport {
  run: string
  table:
    | {
        store: string
        columns: string[]
        rows: RetrievalMaskRow[]
        corpus_snapshot_ref: string
        benchmark_corpus_snapshot_ref: string
        corpus_drifted: boolean
        arm_executors: Record<string, boolean>
        qrels_sources?: Record<string, number>
        queries?: number
        floors: { min_arm_contribution: number; min_scored_queries: number }
      }
    | null
  contributions: RetrievalArmContribution[] | null
  benchmark: { name: string; store: string; queries: unknown[] } | null
}
export interface RetrievalBenchView {
  stores: Record<string, RetrievalStoreReport>
  arms: string[]
  masks: string[]
  control_mask: string
  arm_verdicts: string[]
  k: number
  floors: { min_arm_contribution: number; min_scored_queries: number }
}
export interface RetrievalLabelCard {
  store: string
  benchmark: string
  candidates_per_query: number
  labelled: number
  mined: number
  queries: {
    query: string
    source: string
    already_relevant: string[]
    candidates: string[]
  }[]
}
export interface StudyRow {
  study_id: string
  kind: string
  subject: Record<string, unknown>
  hypothesis: string
  k: number
  registered_ts: number
  verdict: string | null
  agreement: number | null
  agreement_floor: number
  win_rate: number | null
  low_power: boolean
  fail_reason: string
  locked_regressions: string[]
}
export interface StudyPair {
  case_id: string
  trial: number
  slot_a_arm: string
  direct_winner: string
  swapped_winner: string
  outcome: string
  judgeable: boolean
  agreed: boolean
  position_flipped: boolean
  cost_usd: number | null
}
export interface StudyCaseRun {
  case_id: string
  outcome: string
  pairs: StudyPair[]
}
export interface StudyVerdict {
  verdict: string
  wins: number
  losses: number
  ties: number
  no_signal: number
  win_rate: number | null
  agreement: number | null
  agreement_floor: number
  judge_below_floor: boolean
  low_power: boolean
  fail_reason: string
  detail: string
  k: number
  decided_cases: number
  locked_regressions: string[]
  ledger_row_written: boolean
}
export interface StudyView {
  study_id: string
  kind: string
  subject: Record<string, unknown>
  hypothesis: string
  k: number
  inputs: string[]
  metric: string
  decision_rule: string
  rubric_sha256: string
  registration_sha256: string
  agreement_floor: number
  budget_usd: number
  registered_ts: number
  locked_check_count: number
  status: 'registered' | 'complete'
  verdict: StudyVerdict | null
  runs: StudyCaseRun[]
  evidence: Record<string, unknown> | null
}
export interface IdentityReportSection<T> { count: number; items: T[] }
export interface IdentityReportFacet {
  text: string; cls: string; stability: number; state: string; updated_at: string; pinned: boolean
}
export interface IdentityReportLesson { text: string; category: string; updated_at: string }
export interface IdentityReportSkill {
  name: string; uses: number; last_used: string; used_in_window: boolean
  aging_state: string; created_at: string
}
export interface IdentityReportProposal { label: string; kind: string }
export interface IdentityReport {
  period: { window_days: number; since: string; until: string }
  window_days: number
  generated_at: string
  total: number
  facets: IdentityReportSection<IdentityReportFacet>
  lessons: IdentityReportSection<IdentityReportLesson>
  skills: IdentityReportSection<IdentityReportSkill>
  proposals: IdentityReportSection<IdentityReportProposal>
  memory: Record<string, number>
  narrative: string
  narrative_status: 'skipped' | 'written' | 'unavailable'
  markdown: string
}
export interface IdentityReportView extends IdentityReport {
  cadence: '' | 'monthly' | 'weekly' | 'off'
}
export interface IdentityReportDelivery {
  artifact_slug: string
  artifact_version: number
  inbox_item_id: string
  report: IdentityReport
}

export interface LearningHealth {
  days: number
  composite: {
    score: number | null
    components: HealthComponent[]
    measured: number
    of: number
    ideal_band: [number, number]
  }
  utilization: { samples: number; mean: number | null; ideal_band: [number, number] }
  capture: { days: number; passes: number; errors: number; cost_usd: number; all_ok_streak: number }
  surfacing: { surfaced: number; used: number; precision: number | null }
  cost_by_op: { op: string; passes: number; cost_usd: number }[]
  judge: {
    runs_scanned: number
    verdicts: number
    divergences: number
    false_pass_rate: number | null
    nodding_gates: { template: string; node: string; detail: string }[]
    mae: { buckets: MaeBucket[]; labelled: number; unlabelled: number; no_confidence: number }
  }
  attribution: {
    proposers: {
      source: string
      counts: Record<string, number>
      total: number
      decided: number
      harm_rate: number
      effective_rate: number
    }[]
    history: { source: string; verdict: AttributionVerdict }[]
  }
  ablation: { at?: string; rows?: { heuristic: string; delta: number; verdict: string; items: number }[] }
}

export interface WeekOccurrence {
  trigger_id: string
  trigger_name: string
  at: number
  suppressed_by: '' | 'quiet' | 'skipped' | 'off_duty'
  reason: string
}

export interface WeekProjection {
  start: string
  end: string
  server_tz: string
  occurrences: WeekOccurrence[]
  truncated: string[]
}

export interface EventFireResult {
  ok: boolean
  result: { ran: boolean; reason: string; success?: boolean; exit_code?: number; stdout?: string; stderr?: string; error?: string; duration_ms?: number }
}
export type KnowledgeType =
  | 'note' | 'fleeting' | 'journal' | 'gist' | 'bookmark'
  | 'image' | 'audio' | 'video' | 'pdf' | 'document' | 'sheet' | 'slides'
  | 'artifact'
  | 'decision'
export interface KnowledgeEntity { id: string; name: string; entity_type?: string; description?: string }
export interface KnowledgeRelation { id: string; source_name?: string; target_name?: string; relation_type?: string; weight?: number }
export interface KnowledgeItem {
  id: string; title?: string; content?: string; summary?: string
  item_type?: string; tags?: string[]
  provider?: string; status?: string
  source_id?: string | null; guid?: string | null
  is_pinned?: boolean; is_archived?: boolean
  read_state?: 'unread' | 'reading' | 'read'; favorited?: boolean
  created_at?: string; updated_at?: string
  _score?: number; _match_type?: string; ranking?: RecallRankingDisclosure
  kind?: string | null
  type?: KnowledgeType; gist_language?: string; url?: string; url_title?: string
  mime_type?: string; file_size?: number; thumbnail_path?: string; file_path?: string; word_count?: number
  file_metadata?: { width?: number; height?: number; format?: string; page_count?: number; sheet_count?: number; slide_count?: number; row_count?: number; line_count?: number } & Record<string, unknown>
  insights?: Record<string, unknown> | null; ai_summary?: string; ai_title?: string
  processing_status?: string; processing_error?: string
  content_truncated?: boolean
  has_embedding?: boolean
  entities?: KnowledgeEntity[]; relations?: KnowledgeRelation[]
  score?: number
  chunk_index?: number
  neighbour_chunk_index?: number
  shared_entities?: number
}
export interface KnowledgeRsvpState {
  item_id: string; title: string; word_index: number; wpm: number; chunk_size: 1 | 2
  bookmark_index: number | null; word_count: number; content_revision: string
  content_changed: boolean; updated_at: string | null
}
export interface ResearchScope {
  tags: string[]
  window_secs: number
}

export interface ResearchReport {
  id: string
  name: string
  prompt: string
  schedule: { kind: string; every_secs?: number | null; at_ts?: number | null; cron_expr?: string | null }
  tz: string
  source: ResearchScope
  context: ResearchScope | null
  citation_policy: 'cite-source-only' | 'allow-citing-context'
  iteration_cap: number
  enabled: boolean
  created_ts: number
  last_run_ts: number | null
  last_status: string
  last_error: string
  watermark_ts: number
}

export type ResearchReportInput = Omit<
  ResearchReport,
  'id' | 'created_ts' | 'last_run_ts' | 'last_status' | 'last_error' | 'watermark_ts'
>

export interface KnowledgeStaleness {
  item_id: string
  stale: boolean
  new_source_items: number
  changed_sources: number
  checked_at: string
  scope: string
}

export interface KnowledgeIngestGraph {
  item_type: string
  nodes: { node_type: string; backend?: string; model_backed?: boolean; terminal?: boolean }[]
  edges: { from: string; to: string; when?: string; loop?: boolean; max_iters?: number }[]
  processing_status?: string
  node_phases?: Record<string, string>
}
export interface ExtractedContent {
  id: string; item_id: string; node_type: string; backend?: string
  text?: string; metadata?: Record<string, unknown>; created_at?: string
}
export interface KnowledgeIntent {
  id: string; goal?: string; enabled?: boolean
  enabled_for?: string[]; propose_skill?: boolean
  outcome_count?: number
}
export interface SourceRemediation {
  kind: string
  guidance: string
  detail: string
  action: string
}
export interface WatchedSource {
  id: string; name: string; provider: string; kind: string
  spec: Record<string, unknown>; budget: Record<string, unknown>
  enrichment: string
  poll_interval_secs: number; item_type: string; enabled: boolean
  created_at?: string; updated_at?: string
  last_poll_at?: string | null; next_poll_at?: string | null
  last_new_count?: number
  health_status?: string
  last_error_summary?: string
  last_escalations?: string[]
  enrolled: boolean
  event_driven?: boolean
  remediation: SourceRemediation
}
export interface SourceKind {
  provider: string; display_name: string; kind: string
  form: string
  previewable: boolean
  poll_interval_secs: number
  default_item_type: string
  detectors?: string[]
  max_requests?: number
  formats?: string[]
  presets?: string[]
  default_include?: string[]
  max_files?: number
  guidance?: Record<string, string>
}
export interface SourcesResponse {
  sources: WatchedSource[]
  kinds: SourceKind[]
  health_statuses: string[]
  raw_enrichment: string
}
export interface SourceRecipe {
  id: string; displayName: string; description: string
  provider: string; kind: string
  itemType: string; enrichment: string
  matchPatterns?: string[]
  urlGuidance?: string
  spec: Record<string, unknown>
  tags?: string[]
  groups?: Record<string, string>
}
export interface SourceRecipesResponse {
  recipes: SourceRecipe[]
  matches?: SourceRecipe[]
  url?: string
}
export interface SourcePreviewItem {
  guid: string; title: string; url: string; published_at: string; snippet: string
}
export interface SourcePreviewResult {
  items: SourcePreviewItem[]
  detector: string
  escalations: string[]
  requests_used: number
  guidance: string
  health_status: string
  error: string
}
export interface IntentOutcomeField { name: string; type: string; value: unknown }
export interface IntentOutcome {
  id: string; intent_id: string; intent_name?: string
  item_id: string | null; item_title?: string
  takeaway?: string; fields?: IntentOutcomeField[]; created_at?: string
}
export interface KnowledgeStats { items: number; entities: number; relations: number; embeddings: { enabled: boolean; model?: string; embedded_items?: number; stale_items?: number } }
export type InboxClassification = 'needs_reply' | 'fyi' | 'noise'
export type InboxConfidence = 'high' | 'needs_review' | 'escalate' | 'user'
export type InboxItemStatus = 'pending' | 'seen' | 'sent' | 'dismissed' | 'handled' | 'filtered'
export type InboxItemKind =
  | 'message' | 'mention' | 'email' | 'agent_request'
  | 'proposal' | 'needs_input' | 'digest' | 'system' | 'user_note'
export interface InboxThreadMsg { sender_name?: string; text?: string; ts?: string }
export interface InboxItem {
  id: string; channel: string; channel_name: string; thread_ts?: string | null
  message: string; sender_id: string; sender_name: string
  thread_context?: InboxThreadMsg[]
  classification: InboxClassification; draft?: string; confidence: InboxConfidence
  status: InboxItemStatus; created_at?: number; context_summary?: string; ts?: string
  source?: string; can_reply?: boolean; reply_target?: string
  favorited?: boolean
  feedback_producers?: Record<'classification' | 'draft' | 'digest', FeedbackProducer | undefined>
  item_kind?: InboxItemKind
  refs?: Record<string, any>
  owner?: string
  owner_states?: Record<string, InboxItemStatus>
}
export interface InboxProposal {
  title: string
  preview: string
  preview_kind: 'text' | 'diff'
  provenance: string
  expires_at?: string | null
  editable: boolean
  apply: Record<string, Record<string, unknown>>
}
export interface InboxProposalApplyResult {
  ok: boolean
  case?: string
  result?: Record<string, unknown>
  error?: string
  item?: InboxItem
}
export interface InboxKindCount { kind: InboxItemKind; total: number; open: number; channel: boolean }
export interface InboxProvider { name: string; display_name: string; source_name: string }
export interface InboxHealth { running: boolean; last_poll_at?: number; last_poll_ok?: boolean; last_error?: string; poll_count?: number; stale?: boolean }
export interface InboxSourceHealth { name: string; active: boolean; kind: 'push' | 'poll'; can_reply: boolean }
export interface InboxStatus {
  enabled: boolean; user_id?: string
  native_source_active?: boolean; sources?: InboxSourceHealth[]
  watched_channels?: Array<{ id: string; name: string }>
  pending_count: number; open_count: number; total_count: number; health: InboxHealth
  poll_interval_seconds?: number
  owner?: string
  shared?: boolean
  mine_count?: number
}
export interface InboxSettings {
  auto_cleanup_enabled: boolean
  retention_days: number
}
export interface SelEvent {
  event_id: string; timestamp: string; event_type: string; caller_identity?: string
  agent?: string; source?: string; operation?: string; tool_kind?: string; outcome?: string
  resources?: string; error?: string; prev_hash?: string; entry_hash?: string
  downstream_service?: string; request_id?: string; integrity_ok?: boolean
  metadata?: { reason?: string } & Record<string, unknown>
}
export interface AuditPage {
  events: SelEvent[]; count: number; next_cursor: string; scanned: number; truncated: boolean
  outcome_families: { key: string; label: string; values: string[] }[]
}
export interface AuditFilters {
  caller?: string; operation?: string; outcome?: string; downstream_service?: string
  since?: string; until?: string
}
export interface SelVerify {
  ok: boolean; checked: number; valid?: number; tampered?: number; windowed?: boolean
  window?: number | null
}
export interface ComputerUseTrailPoint {
  seq: number; ts: number; tool: string; app: string
  method: 'ax_press' | 'located' | 'global'
  x: number | null; y: number | null; label: string
}
export interface ComputerUseElement {
  index: number; role: string; title: string; enabled: boolean
  frame: { x: number; y: number; width: number; height: number } | null
}
export interface ComputerUseSnapshot {
  snapshot_id: string; app: string; age_secs: number; expired: boolean
  element_count: number; elements?: ComputerUseElement[]
}
export interface ComputerUseFeedRow {
  timestamp: string; operation: string; outcome: string; error: string
  source: string; caller_identity: string; app: string
}
export interface ComputerUseLiveView {
  enabled: boolean; allowed_apps: string[]; ttl_secs: number
  snapshots: ComputerUseSnapshot[]; trail: ComputerUseTrailPoint[]; feed: ComputerUseFeedRow[]
}

export interface SessionArchive { name: string; key: string; stamp: string; size: number; mtime: number }

export interface SessionTemplate {
  id: string; name: string; agent: string; model: string
  reasoning_effort: string; first_prompt: string; created_at: number
}
export type SessionTemplateInput = Omit<SessionTemplate, 'id' | 'created_at'>
export interface PortabilityManifest {
  version: number; format: string; created_at: string; hostname: string; user: string
  contents: Record<string, number>
  scope?: 'full' | 'partial'
  domains?: string[]
  domain_counts?: DurabilityDomainCounts
  excluded?: string[]
  verified?: boolean
}
export interface DurabilityImportResult {
  ok: boolean
  applied?: boolean
  error?: { code: string; message: string }
  summary?: { mode: string; items: string[]; refused?: string[]; pre_restore?: string }
  manifest?: PortabilityManifest
}
export interface DurabilityRestoreResult {
  ok?: boolean
  plan?: boolean
  error?: { code: string; message: string }
  [k: string]: unknown
}
export interface ProjectImportIssue { path: string; code: string; message: string; fatal: boolean }
export interface ProjectImportResult {
  project_name: string; accepted: string[]; refused: ProjectImportIssue[]
  secrets_expected: string[]; ok: boolean; summary?: string; preview?: boolean
  project_id?: string; written?: string[]; error?: string
}
export type UpdateState = 'idle' | 'applying' | 'applied' | 'failed' | 'rolling_back' | 'rolled_back'
export interface UpdateCheck { available: boolean; changes: string; checked: boolean; auto_update: boolean; version?: string; latest?: string; kind?: 'git' | 'pip' | 'container' | 'desktop'; current?: string; update_available?: boolean; commits_behind?: number | null; apply_method?: string; instructions?: string[]; update_dev_mode?: boolean; release_notes?: string; update_state?: UpdateState; update_from_version?: string; update_target?: string; update_started_at?: number | null; update_updated_at?: number | null; update_error?: string; rollback_available?: boolean; rollback_version?: string }
export interface UpdateActionResult { ok?: boolean; status?: string; kind?: string; detail?: string; error?: string }

export interface NotificationSettings {
  mute_all: boolean; quiet_hours_enabled: boolean; quiet_hours_start: string; quiet_hours_end: string
  min_severity: string
}
export type NotificationMode = 'never' | 'badge' | 'immediate' | 'digest'
export type NotificationTarget = 'dashboard' | 'push' | 'native'
export type NotificationSound = 'turn_complete' | 'approval_needed' | 'error' | 'coin_blip' | 'terminal_bell'
export interface NotificationRuleRow {
  key: string; source: string; kind: string; label: string; severity: number
  mode: NotificationMode
  default_mode: NotificationMode
  configured: boolean
  targets: NotificationTarget[]
  conditions: { keywords: string[]; name_mention: boolean }
  sound: NotificationSound | null
}
export interface NotificationRulesDoc {
  rules: NotificationRuleRow[]
  digest: { schedule: string }
  targets: NotificationTarget[]
}
export interface NotificationRulePatch {
  mode?: NotificationMode
  targets?: NotificationTarget[]
  conditions?: { keywords?: string[]; name_mention?: boolean }
  sound?: NotificationSound | null
}
export type MemoryVaultMode = 'off' | 'mirror' | 'two_way'
export interface MemorySettings { history_idle_hours: number; history_max_days: number; migrated?: boolean; l1_manifest?: boolean; active_recall?: boolean; proactive_commitments?: boolean; vault_mode?: MemoryVaultMode; vault_path?: string; graph_enabled?: boolean; push_context?: boolean; push_min_confidence?: number; graph_topology_in_context?: boolean; holder_attribution?: boolean; slot_size_cap?: number }


export type TriageDigestState = 'uninstalled' | 'off' | 'never_run' | 'ready' | 'error'

export interface TriageAutoDone {
  ordinal: string; source_id: string; action_type: string; provider: string
  rule: string; reversal: string; undoable: boolean; ok: boolean; error: string
  permalink: string; title: string; source: string; item_permalink: string; materiality: string
}

export interface TriagePending {
  ordinal: string; action_type: string; tier: string; pattern_key: string; clamped: boolean
  reason: string; rule: string; answered: boolean; answer: string
  permalink: string; title: string; source: string; item_permalink: string; materiality: string
}

export interface TriageLedgerRow {
  kind: string; seq: number; ordinal: string; action_type: string; rule: string
  outcome: string; reason: string; detail: string; verb: string; permalink: string
}

export interface TriageSchedule { id: string; name: string; cron: string; enabled: boolean; created_by: string }

export interface DecisionRow {
  id: string; summary: string; status: string; domain: string
  expectation: string; confidence: number | null
  review_horizon: string; reminder_trigger_id: string | null
  deferrals: number
  stale_pending: boolean
  outcome: string | null; outcome_grade: string | null; outcome_captured_at: string | null
  lesson_memory_key: string | null
  created_at: string
  overdue?: boolean
}

export interface CalibrationBucket {
  n: number
  better: number; as_expected: number; worse: number
  mean_confidence: number | null
  as_expected_rate: number | null
  count_honest: boolean
}

export interface DecisionJournalView {
  decisions: DecisionRow[]
  calibration: Record<string, CalibrationBucket>
  calibration_min_n: number
  statuses: string[]; domains: string[]; grades: string[]
}

export interface TriageDigestView {
  state: TriageDigestState
  enabled: boolean
  installed: boolean
  error: string
  workflow?: string
  node_id?: string
  schedule?: TriageSchedule | null
  schedule_drift?: boolean
  run_id?: string
  status?: string
  finished_at?: string
  permalink?: string
  window_start?: string
  title?: string
  body?: string
  handed_to_notify?: boolean
  quiet_hours?: { known: boolean; enabled: boolean; start: string; end: string; mute_all: boolean }
  collected?: number
  lanes?: Record<string, number>
  dropped?: number
  auto_stage_ran?: boolean
  auto_done?: TriageAutoDone[]
  pending?: TriagePending[]
  budget_breached?: boolean
  budget_reason?: string
  degraded?: boolean
  machine_did?: TriageLedgerRow[]
  ledger_complete?: boolean
  ledger_rows?: number
}

export interface TriageReplyResult {
  ok: boolean
  outcome: 'acted' | 'help'
  help_reason?: string
  help?: string
  results?: Array<{
    ordinal: string; outcome: 'acted' | 'already' | 'unknown'; verb?: string
    executed?: boolean; detail?: string; rule?: string; rule_error?: string
    recorded?: boolean
  }>
}

export interface ApprovalRuleRow {
  key: string; pattern: string; verdict: 'approve' | 'deny' | 'suppressed'; scope: string
  hit_count?: number; expires_at?: string | null; send_capable?: boolean
  created_from_digest?: string | null; specificity?: number
  created_at?: string | null; updated_at?: string | null
  suppressed_until?: string | null; suppression_rung?: number
}

export interface VolunteerArmStat { n: number; used: number; precision: number }
export interface VolunteerStats {
  arms: Record<string, VolunteerArmStat>
  overall: VolunteerArmStat
  enabled: boolean
  min_confidence: number
}
export interface MemoryVaultStatus { enabled: boolean; mode: MemoryVaultMode; path: string; files: number; exists: boolean }
export interface MemoryVaultSyncResult { records: number; files: number; written: number; pruned: number; path: string; mode: MemoryVaultMode; absorbed: number; rejected: number; conflicts: number; raw_ingested: number; seeded: number }
export interface DailyDigest { day: string; text: string; created_at: string }
export interface MemoryStats {
  semantic_active: number; semantic_deleted: number; episodic_active: number; episodic_deleted: number
  events_count: number; embedded_count: number; embedding_provider?: string; has_legacy_memory?: boolean; migrated?: boolean
}
export interface SemanticEntry { key: string; value_json?: string; created_at?: string; updated_at?: string; confidence?: number; source?: string; scope?: string; scope_ref?: string; tier?: string; recall_count?: number; contributor?: string; is_mine?: boolean }
export interface EpisodicEntry { id: string; text: string; tags?: string; conversation_id?: string; importance?: number; created_at?: string }
export interface MemoryEvent {
  id: number; event_type: string; memory_type: string; memory_key?: string
  old_value?: string; new_value?: string; source?: string; created_at?: string
  undone_at?: string | null
}
export interface MemoryContextPreview { semantic_context: string; episodic_context: string }
export interface MemoryLintFlag { check: string; key: string; detail: string }
export interface MemoryLint { auto_fixed: Record<string, number>; flags: MemoryLintFlag[]; flag_count: number }
export type MemoryEntityType = 'person' | 'project' | 'tool' | 'org' | 'topic' | 'place'
export interface MemoryEntity {
  id: string
  name: string
  entity_type: MemoryEntityType
  aliases: string[]
  source: string
  inbound_count: number
  last_linked_at?: string | null
}
export interface MemoryLink {
  id: number
  from_kind: string
  from_ref: string
  to_entity: string | null
  to_ref: string | null
  link_type: string
  provenance: string
  confidence: number
  context: string | null
  created_at: string
}
export interface MemoryGraphSummary {
  entities: number
  links: number
  linked_records: number
  proposals: number
  semantic_orphans: number
  episodic_orphans: number
  phantom_entities: number
}
export interface MemoryEntitiesResponse {
  entities: MemoryEntity[]
  summary: MemoryGraphSummary | Record<string, never>
  enabled: boolean
}
export interface MemoryGraphRebuild {
  ok: boolean
  seeded: { from_facts: number; from_knowledge: number }
  records_processed: number
  links_created: number
  before: MemoryGraphSummary
  after: MemoryGraphSummary
}
export interface MemoryObservability {
  stats: Record<string, number>
  rejections: Record<string, number>
  context_preview: { semantic_chars: number; episodic_chars: number; lessons_chars: number; total_chars: number; semantic_preview?: string; episodic_preview?: string; lessons_preview?: string }
}
export type LessonStanding = 'injected' | 'retained'
export interface Lesson {
  rule: string; category: string; ts?: string
  standing?: LessonStanding; confidence?: number; confidence_reason?: string
  observations?: number; contradictions?: number; reversals?: number
}
export interface MemoryGraphNode { id: string; label: string; group?: string; title?: string; ref?: string }
export interface MemoryGraphEdge { from: string; to: string }
export interface MemoryGraphData { nodes: MemoryGraphNode[]; edges: MemoryGraphEdge[] }
export interface MemoryEntityNode {
  id: string
  name: string
  entity_type: MemoryEntityType
  aliases: string[]
  community: number | null
  inbound_count: number
}
export interface MemoryEntityEdge {
  from: string
  to: string
  records: number
  link_types: string[]
  provenances: string[]
  confidence: number
}
export interface MemoryEntityGraph {
  nodes: MemoryEntityNode[]
  edges: MemoryEntityEdge[]
  enabled: boolean
}
export interface MemoryRecordLink extends MemoryLink { entity_name: string }
export interface MemoryEntityProposal {
  name: string
  mention_count: number
  first_seen_at: string
  last_seen_at: string
  refs?: string
}
export interface MemorySlotLine {
  text: string
  added_at: string
  tombstoned: boolean
  tombstoned_by: string
  reinforcements: number
}
export interface MemorySlot {
  name: string
  title: string
  description: string
  cap_chars: number
  scope: string
  builtin: boolean
  materialized: boolean
  live_chars: number
  live_count: number
  lines: MemorySlotLine[]
}
export interface MemorySlotsResponse { slots: MemorySlot[]; block_limit: number }
export interface MemorySlotAppendResult {
  ok: boolean
  lines?: MemorySlotLine[]
  error?: string
  proposal?: MemorySlotTrimProposal
}
export interface MemorySlotTrimProposal {
  slot: string
  cap_chars: number
  current_chars: number
  incoming_chars: number
  over_by: number
  drop_candidates: string[]
  message: string
}
export interface SecurityStats { denied_commands: number; suspicious_patterns: number; tool_schemas: number; redaction_paths: number }
export interface DenylistBaseline {
  version: number
  sha256: string
  count: number
  verified: boolean
  detail: string
}
export interface DeniedCommands {
  builtin: string[]
  user: string[]
  baseline: DenylistBaseline
  user_additions: number
}
export interface EgressPolicyConfig { allow_hosts: string[]; deny_hosts: string[]; allow_private: boolean }
export interface CredentialStoreState {
  migration: string
  backend: 'keychain' | 'dotenv'
  requested: 'keychain' | 'dotenv'
  blocked: boolean
  pending_keys: string[]
  pending: number
  keychain_keys: number
  rollback_available: boolean
  snapshot_name: string
  verified: boolean
  verification: { checked: number; missing: string[]; still_in_dotenv: string[] }
}
export interface CredentialMoveResult extends CredentialStoreState {
  ok: boolean
  reason: string
  moved: string[]
  already: string[]
  failed: string[]
}
export interface SecretConsumerWire {
  kind: 'workflow' | 'trigger'
  id: string
  label: string
}
export interface SecretPresenceWire {
  name: string
  scope: 'global' | 'project' | 'host'
  project_id: string
  present: true
  inherited_from_host: boolean
  consumers: SecretConsumerWire[]
}
export interface SecretsVaultState {
  secrets: SecretPresenceWire[]
  counts: { total: number; global: number; project: number; host: number }
  empty_hint: string
}
export interface SecretWriteResult {
  secret: SecretPresenceWire | Record<string, never>
  secrets: SecretPresenceWire[]
}
export interface SecretDeleteResult {
  deleted: string
  project_id: string
  secrets: SecretPresenceWire[]
}
export type ProjectionStrategy = 'log' | 'diff' | 'json' | 'test' | 'csv' | 'code'
export interface ProjectionRule {
  name: string
  match_regex: string
  strategy: ProjectionStrategy
  head?: number
  tail?: number
  keep?: string
  skip?: string
  count?: string
}
export type FeedbackTargetKind =
  | 'inbox_classification' | 'inbox_draft' | 'inbox_digest'
  | 'loop_finding' | 'routing_suggestion' | 'proposal_content' | 'app_judgment'
export interface FeedbackProducer { producer_kind: string; producer_id: string }
export interface FeedbackRecordBody {
  target_kind: FeedbackTargetKind
  target_id: string
  verdict: 'up' | 'down'
  reason?: string
  snapshot?: Record<string, unknown>
  producer_kind?: string
  producer_id?: string
}
export interface FeedbackProducerRow {
  producer_kind: string
  producer_id: string
  ups: number
  downs: number
  n: number
  accuracy?: number
  suppressed?: boolean
  proposal_only?: boolean
  collecting?: boolean
}
export interface FeedbackProducersResponse {
  producers: FeedbackProducerRow[]
  min_n: number
  window_days: number
}
export interface InvestigateOrigin { kind: string; title: string; back_link: string }

export interface ToolsSavings {
  saved_chars: number
  saved_tokens_estimated: number
  estimated: boolean
  projection_count: number
  top_compressor: string | null
  by_compressor: Record<string, number>
  rows: unknown[]
}

export interface ToolGroupInfo {
  name: string
  display: string
  alwaysOn: boolean
  toolCount: number
  tools: string[]
  capability: string
  offerable: boolean
  instructions: string
}

export interface ToolGroupsData {
  enabled: boolean
  groups: ToolGroupInfo[]
  surfaceDefaults: Record<string, string[]>
}

export interface SystemAgentStats {
  sessions_created: number; sessions_cleaned: number
  subagents_spawned: number; subagents_completed: number; subagents_failed: number
  input_tokens: number; output_tokens: number
  cache_creation_tokens: number; cache_read_tokens: number
  total_turns: number; total_duration_ms: number
}
export interface SystemInfo {
  hostname: string; version?: string; os: string; platform: string; python: string; arch: string; pid: number; cpu_count: number; cwd: string
  mem_total_gb: number; proc_mem_mb: number; mem_free_gb: number; mem_used_gb: number
  load_1m: number; load_5m: number; load_15m: number; cpu_pct: number; proc_cpu_pct?: number; ip?: string
  disk_total_gb?: number; disk_free_gb?: number
  gpu_present?: boolean; gpu_vendor?: string; gpu_model?: string
  net_rx_kbs?: number; net_tx_kbs?: number
  thread_count?: number; child_processes?: number; mcp_total?: number
  mcp_processes?: { sandbox: number; agent_cli: number; mcp_server: number }
  stats?: SystemAgentStats
}
export interface AuthStatus { mode: string; bind_host: string; valid: boolean; minutes_remaining?: number; oauth2_issuer?: string }

export interface PendingApproval {
  id: string; source: string; tool: string
  tool_input?: unknown; tool_purpose?: string
  session: string; ts: number
}

export interface PushStatus {
  backend: 'webpush' | 'ntfy' | 'relay' | 'none'
  vapid_public_key: string
  vapid_ready: boolean
  ntfy_configured: boolean
  relay_configured: boolean
  relay_devices: string[]
  approval_targeted: boolean
  devices: string[]
  subscribed: number
}

export interface DashboardStatus {
  uptime: string; uptime_secs?: number; start_time?: number
  sessions?: number; messages?: number; triggers?: number; lessons?: number; subagents?: number
  update_available?: boolean; version?: string; platform?: string
  update_progress?: { step: string; detail?: string } | null
  yolo?: boolean; yolo_expires_in?: number
  os_type?: string; arch?: string; cpu_count?: number; mem_total_gb?: number
  stats?: SystemAgentStats
}

export interface SettingsProvider {
  name: string; displayName?: string; description?: string; version?: string; author?: string
  enabled: boolean; error?: string; available?: boolean; unavailableReason?: string
  managed?: boolean
  provider?: { type?: string; entity?: string; capabilities?: string[]; multiInstance?: boolean; hasConfigSchema?: boolean }
  tags?: string[]
}
export interface AgentRuntime {
  name: string; provider_id: string; type: string; extension: string | null
  ready: boolean; state: string; detail: string; login_command: string[] | null
}
export interface RunnerHealth {
  ok: boolean; probe: string; checked_at: string
  version: string | null; latency_ms: number | null; error: string | null
  resolved_command: string[]
}
export interface RunnerCapabilities {
  source: string; recorded_at: string
  models: string[]; permission_modes: string[]; efforts: string[]
}
export interface RunnerRow {
  id: string; display_name: string; runtime_id: string; source: string
  dialect: string; bin_names: string[]
  health: RunnerHealth | null
  health_stale: boolean | null
  capabilities: RunnerCapabilities | null
  adapter: { npm_pkg: string; pinned: boolean; state: string; verified: boolean; detail: string }
  lease: RunnerLease | null
}
export interface RunnerLease {
  holder: string; taken_at: number; expires_at: number; renewals: number
  age_secs: number; expires_in_secs: number
}
export interface ProviderSchemaProp extends SchemaProp {
  enum?: string[]; minimum?: number; maximum?: number
  minLength?: number; maxLength?: number; pattern?: string
}
export interface ProviderSchema { type?: string; properties?: Record<string, ProviderSchemaProp>; required?: string[] }
export interface ProviderInstance { id: string; extension_name: string; display_name: string; config: Record<string, unknown>; enabled: boolean; _secret_set?: string[] }
export interface ModelProvider { name: string; type: string; model?: string; capabilities: string[]; credential_status: string }
export interface ModelProviderType {
  type: string
  label: string
  app: string
  capabilities: string[]
  multiInstance: boolean
  settingsSchema: { properties?: Record<string, ModelProviderTypeField>; required?: string[] }
}
export interface ModelProviderTypeField extends SchemaProp {
  default?: string
  enum?: string[]
}
export interface OllamaLocalModel {
  name: string; size: number; size_human?: string; modified_at?: string
  parameter_size?: string; quantization?: string; family?: string
}
export interface OllamaSearchResult { name: string; description?: string; pulls?: number; tags?: string[] }
export interface OllamaModelInfo {
  model: string; family?: string; parameter_size?: string; quantization?: string
  format?: string; context_length?: number; capabilities?: string[]; license_short?: string; error?: string
}
export interface SearchCapabilitiesInfo {
  returns_content: boolean; returns_answer: boolean; returns_highlights: boolean
  supports_recency: boolean; supports_domains: boolean; supports_fetch: boolean; depths: string[]
}
export interface SearchProviderInfo { name: string; display_name: string; capabilities: SearchCapabilitiesInfo; available: boolean }

export interface CapabilityMatrix {
  word_timestamps?: boolean; segment_timestamps?: boolean; speaker_labels?: boolean
  acoustic_events?: boolean; hotword_biasing?: boolean; hotword_budget?: number
  languages?: string[]; reasoning_budget_control?: boolean
}
// The catalog-contract fields (matrix/license/…, LMMV §2) are optional — only local
export interface AvailableModel {
  id: string; name: string; capabilities: string[]; provider: string; provider_type: string
  size?: number; downloaded?: boolean; gated?: boolean; description?: string; size_mb?: number; source?: string
  matrix?: CapabilityMatrix | null; license?: string; non_commercial?: boolean
  runtime?: string; runtime_contract?: string; context_tokens?: number; output_tokens?: number
  io_mime?: Record<string, unknown>; status?: string; integrity?: string; config_only?: boolean
  fit?: ModelFitVerdict; fit_reason?: string; fit_need_mb?: number; quoted_size_mb?: number
  fit_step_down?: string | null
  host_fit?: HostModelFit
}
export type ModelFitVerdict = 'green' | 'yellow' | 'red' | 'unknown'
export interface HostModelFit {
  budget_mb: number | null
  total_ram_mb: number
  unified_memory: boolean
  gpu_model: string
  measured: boolean
  hide_unrunnable: boolean
}
export interface ProviderModels {
  name: string; displayName?: string; type: string; models: AvailableModel[]
  error?: string; searchable?: boolean; local?: boolean
  host_fit?: HostModelFit
}
export interface AvailableModelsResponse { providers: ProviderModels[]; fit?: HostModelFit }
export interface ProviderTestResult { ok: boolean; status?: string; message: string }
export interface LocalModelTokenStatus {
  configured: boolean
  source: 'credential_store' | 'environment' | 'huggingface_cache' | 'none' | string
  masked_token: string
  state: 'valid' | 'invalid' | 'unavailable' | 'unconfigured' | string
  valid: boolean | null
  username: string
  error: string
  cached: boolean
  checked_at: number
  expires_at: number
}
export interface LocalModelTestFailure {
  code: string
  message: string
  retryable: boolean
}
export interface LocalModelCapabilityTest {
  capability: string
  ok: boolean
  detail: string
  elapsed_ms: number
  failure?: LocalModelTestFailure | null
}
export interface LocalModelSelfTestResult {
  provider: string
  display_name?: string
  ok: boolean
  tests: LocalModelCapabilityTest[]
  failure: LocalModelTestFailure | null
}
export interface LocalModel { name: string; id: string; size_mb: number; size: number; description: string; downloaded: boolean; capabilities: string[]; gated: boolean; source: string }
export interface DownloadJob {
  id: string; provider: string; model: string
  kind: 'weights' | 'sidecar-install'
  state: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  progress: number; speed_bps: number; eta_s: number
  total_bytes: number; downloaded_bytes: number
  error: string; reason: string
}
export interface ReindexJob {
  id: string; model: string; status: 'running' | 'done' | 'error'
  phase: string; done: number; total: number; knowledge: number; memory: number; error: string
}
export interface LoadedModel {
  provider: string; model: string
  kind: 'in-process' | 'sidecar'
  rss_mb: number | null
  is_active: boolean
  generation?: number; pid?: number
}
export interface MemoryPressure {
  total_mb: number; used_mb: number; available_mb: number; used_pct: number
  warn_pct: number; warn: boolean
  source: 'vm_stat' | 'meminfo' | 'unavailable'
}
export interface ResidentProvider {
  provider: string; display_name: string; ok: boolean
  state: 'ready' | 'loading' | 'unavailable'
  kind: 'in-process' | 'sidecar'
  sidecar: { generation: number; restarts: number; rss_mb: number; alive: boolean } | null
}
export interface ResidencySnapshot {
  loaded: LoadedModel[]; providers: ResidentProvider[]; pressure: MemoryPressure
}
export interface DashboardConfig {
  restore_sessions: boolean; restore_window_minutes: number; merge_queued_messages: boolean
  auto_tag_sessions: boolean
  widget_density: 'more' | 'less'; user_name: string
  username: string
  send_on_enter: boolean; show_timestamps: boolean; show_thinking_inline: boolean
  simplified_tool_names: boolean
  followup_chips: boolean; offer_check_work: boolean; stream_reveal: 'smooth' | 'immediate'
  screen_share_enabled: boolean
  document_editing: boolean
}
export interface OnboardingEssentials {
  model: string | null
  search: boolean
  speech: boolean
  channel: string | null
}
export type OnboardingStep = 'name' | 'essentials' | 'first_success' | 'done'
export interface OnboardingState {
  needs_model: boolean; has_model_provider: boolean; has_chat_binding: boolean
  step?: OnboardingStep
  essentials?: OnboardingEssentials
  first_success?: { knowledge: boolean; trigger: boolean; loop: boolean }
}
export interface OnboardingStatePatch {
  step?: OnboardingStep
  essentials?: Partial<OnboardingEssentials>
  first_success?: Partial<{ knowledge: boolean; trigger: boolean; loop: boolean }>
}
export interface OnboardingImportItem {
  fingerprint: string; source: string; category: string; key: string; title: string
  redactions: number; existing: boolean
}
export interface OnboardingImportSource {
  source: string; display_name: string; root: string; present: boolean; detected: boolean
  counts: Record<string, number>
  items: OnboardingImportItem[]
  secrets_skipped: number; redactions: number
  notes: string[]
}
export interface OnboardingImportScan {
  sources: OnboardingImportSource[]
  categories: string[]
}
export interface OnboardingImportOutcome {
  fingerprint: string; source: string; category: string; key: string
  outcome: 'imported' | 'existing' | 'conflict' | 'rejected'
  destination: string; detail: string
}
export interface OnboardingImportReport {
  counts: Record<string, number>
  results: OnboardingImportOutcome[]
  secrets_skipped: number; redactions: number
  notes: string[]
}
export interface ChatModelOption { name: string; model_id: string; provider: string; description?: string }
export interface SavedAgent {
  name: string; provider: string; provider_agent?: string; acp_mode?: string; model?: string; approval_mode?: string
  description?: string; system_prompt?: string; voice?: string; skills?: string[]; tools?: string[]; triggers?: string[]; source?: string; default_dir?: string; memory_store?: string
  natural_voice?: boolean
  specialty?: string; route_hints?: string
  reserved?: boolean; editable?: boolean
}


export type GoalType = 'verifiable' | 'open_ended' | 'monitor'
export type Granularity = 'quick' | 'balanced' | 'exhaustive' | 'forever'
export interface LoopFinding {
  cycle: number; summary?: string; key_insight?: string
  sources_checked?: string[]; sources_empty?: string[]
  files_touched?: string[]
  new_findings_count?: number; evidence?: string; metric?: { name?: string; value?: number }; ts?: number
}
export interface LoopVerdict {
  cycle?: number; done: boolean; done_reason?: string; marginal_value: number; quality_score: number; regressed: boolean
  adversarial?: boolean; band_used?: number
  verdict?: 'PASS' | 'REJECT' | 'RETRY' | 'REPLAN' | 'ESCALATE' | 'NEEDS_INPUT'
  passed?: boolean; valid?: boolean; invalid_reason?: string; protocol_error?: boolean
  evidence_refs?: string[]; proof?: string
  reasoning?: string; scores?: Record<string, number>; overall?: number
  shortfalls?: string[]; escalated?: boolean; escalation_reason?: string
}
export interface LoopNudge { text: string; sent_at: number; sent_at_cycle: number; applied_cycle: number | null }
export interface RosterMember { role: string; persona: string; role_hint?: string; agent_name?: string }
export interface GoalLoop {
  id: string; name: string; goal: string; sub_goals: string[]; deliverables?: string[]; scope?: string[]
  goal_type: GoalType; intake_rigor: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  attended: boolean; granularity: Granularity
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
  success_criteria: string | null; verify_command?: string
  rubric?: string[]; best_score?: number; last_score?: number | null; ratchet_mode?: string
  marginal_scores?: number[]
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  findings?: LoopFinding[]; verdicts?: LoopVerdict[]; pending_question?: string | null; nudges?: LoopNudge[]
  feedback_producer?: FeedbackProducer
  linked_task_ids?: string[]
  project_id?: string
  tasks_project_id?: string
  session_key?: string
  skill_ids?: string[]; workflow_ids?: string[]; execution_plan?: Record<string, unknown>[]
  unrunnable_commands?: { command: string; missing_binary: string }[]
}
export interface LoopClassification {
  title?: string
  goal_type: GoalType; classified?: boolean; intake_rigor: string; rigor_reason?: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string; strategy_reason?: string
  clarifying_questions?: string[]; verify_command?: string; success_criteria?: string; sub_goals?: string[]
  deliverables?: string[]
  suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]
  execution_plan?: Record<string, unknown>[]
}
export interface LoopValidation {
  can_start: boolean; errors: string[]; warnings: string[]; estimated_cycles?: number; estimated_duration_min?: number
}
export interface LoopIntakeStep { id: string; title: string; prompt: string; answer: string; status: string; discuss: { role: string; content: string }[] }
export interface LoopIntakePhase { id: string; title: string; description: string; steps: LoopIntakeStep[]; status: string }
export interface LoopIntakePlan { phases: LoopIntakePhase[]; current_phase_id?: string; current_step_id?: string }

export type EntryStage =
  | 'ideation' | 'requirements' | 'design' | 'decomposition' | 'implementation'
  | 'verification' | 'review' | 'bugfix' | 'cr_comments' | 'refactor' | 'investigation'
export type ProjectKind = 'greenfield' | 'brownfield'
export const SDLC_STAGES = [
  'ideation', 'requirements', 'design', 'decomposition',
  'implementation', 'verification', 'review',
] as const

export function sdlcStageLabel(stage: string): string {
  const s = (stage || '').trim()
  if (!s) return ''
  if (s === 'cr_comments') return 'CR comments'
  return s.replace(/_/g, ' ')
}
export interface CodeStage {
  stage: string; title: string; objective: string; exit_criteria: string[]
  deliverable: string; task_list_name: string; agent_name?: string
  skill_ids?: string[]; workflow_ids?: string[]
  metric_pass?: number; metric_hold?: number; min_findings?: number; min_dwell_secs?: number
  tasks?: { title: string; description?: string; action_plan?: string[]; exit_criteria?: string[]; depends_on?: number[] }[]
}
export interface CodeFinding {
  cycle: number; summary?: string; key_insight?: string; stage?: string
  stage_label?: string
  task_id?: string
  evidence?: unknown; ts?: number
  files_touched?: string[]
}
export interface CodeProject {
  id: string; name: string; task: string; summary?: string
  entry_stage: EntryStage; project_kind: ProjectKind; intake_rigor: string
  stage_plan: CodeStage[]; stage_status?: Record<string, string>
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  skill_ids?: string[]; workflow_ids?: string[]
  workspace_dir?: string; attended: boolean; autopilot?: boolean
  files_dir?: string
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
  success_criteria: string | null; verify_command?: string; test_command?: string
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  project_id?: string; tasks_project_id?: string; task_list_ids?: Record<string, string>; session_key?: string
  findings?: CodeFinding[]; pending_question?: { question: string; why?: string } | null
  nudges?: { text: string; sent_at?: number; sent_at_cycle?: number; applied_cycle?: number | null }[]
  queued_task_ids?: string[]
}
export interface CodeClassification {
  title?: string; summary?: string; classified?: boolean
  entry_stage: EntryStage; entry_reason?: string; project_kind: ProjectKind
  intake_rigor: string; rigor_reason?: string
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  clarifying_questions?: string[]; verify_command?: string; test_command?: string
  success_criteria?: string; stage_plan: CodeStage[]
  suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]
}

export type VoiceProfileKind = 'clone' | 'design'
export type VoiceLevel = 'explicit' | 'binding' | 'default' | 'built-in'
export interface VoiceProfile {
  id: string; name: string; kind: VoiceProfileKind; provider: string; model: string
  ref_audio: string; ref_text: string
  design_params: Record<string, unknown>; instruct: string
  seed: number; language: string; speed: number
  locked: boolean; locked_at: string
  verified_own_voice: boolean; consent_text: string; consent_audio: string; consent_recorded_at: string
  history: { path: string; seed: number; text_hash: string; created_at: string }[]
  created_at: string; updated_at: string
  artifacts: { ref_audio?: boolean; consent?: boolean; locked?: boolean }
  history_count: number
}
export interface VoiceProfileDraft {
  name?: string; kind?: VoiceProfileKind; provider?: string; model?: string
  ref_text?: string; instruct?: string; design_params?: Record<string, unknown>
  seed?: number; language?: string; speed?: number
}
export type VoiceBindings = Record<string, string>
export interface VoiceResolution {
  surface: string
  resolved: boolean
  level: VoiceLevel
  profile_id?: string; provider?: string; voice?: string
  speed?: number; seed?: number; locked?: boolean
  has_ref_audio?: boolean
}

export type LoopKind = 'general' | 'goal' | 'code' | 'design' | 'research'

export interface CreatedLoopRun {
  run_id: string
  status: string
  blocking: boolean
  kind: LoopKind
}
export type UnifiedLoopStatus =
  | 'intake' | 'planning' | 'review' | 'ready' | 'running' | 'paused'
  | 'stagnant' | 'blocked' | 'needs_input' | 'complete' | 'failed' | 'stopped'
export interface LoopPhase {
  title?: string; stage?: string; objective?: string; exit_criteria?: string[]
  deliverable?: string; tasks?: Record<string, unknown>[]
  [k: string]: unknown
}
export interface LoopSpend {
  dollars_est: number
  turns: number
  tokens: number
  priced: boolean
  planning: { dollars_est: number; turns: number }
}
export interface Loop {
  id: string; kind: LoopKind; name: string; task: string; summary?: string
  spend?: LoopSpend
  intake_rigor?: string
  plan?: LoopPhase[]; phase_status?: Record<string, string>
  execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  strategy_config?: Record<string, unknown>
  agent: string; model: string; provider?: string; provider_agent?: string; reasoning_effort?: string
  skill_ids?: string[]; workflow_ids?: string[]
  workspace_dir?: string; attended: boolean; autopilot?: boolean
  files_dir?: string
  max_cycles: number; max_cost_usd?: number; deadline_secs?: number; idle_secs: number
  stop_reason?: string
  success_criteria: string | null
  status: UnifiedLoopStatus; total_cycles: number; error_message: string | null
  created_at: number; started_at: number | null; completed_at: number | null; elapsed_seconds?: number
  project_id?: string
  tasks_project_id?: string; task_list_ids?: Record<string, string>; linked_task_ids?: string[]; session_key?: string
  findings?: (LoopFinding | CodeFinding)[]; verdicts?: LoopVerdict[]; marginal_scores?: number[]
  nudges?: LoopNudge[]; pending_question?: { question: string; why?: string } | string | null
  feedback_producer?: FeedbackProducer
  kind_config: Record<string, unknown>
}
export interface UnifiedLoopClassification {
  kind: LoopKind; title?: string; summary?: string; classified?: boolean
  intake_rigor?: string; execution: 'solo' | 'multi_agent'; roster?: RosterMember[]; strategy_id?: string
  rigor_reason?: string; strategy_reason?: string; entry_reason?: string
  clarifying_questions?: string[]; suggested_skill_ids?: string[]; suggested_workflow_ids?: string[]
  marketplace_suggestions?: SkillSearchResult[]; success_criteria?: string
  plan?: LoopPhase[]; kind_config: Record<string, unknown>
}

export interface GrillPhaseStep {
  title: string; prompt: string
  kind?: 'text' | 'choice' | 'slider' | 'boundary'
  choices?: string[]; recommended?: string; required?: boolean
  min?: number; max?: number; step?: number
}
export interface GrillPhase { title: string; description: string; steps: GrillPhaseStep[] }
export interface GrillTreeResult { phases: GrillPhase[]; memory_hits: number }

export type PlanStepStatus = 'pending' | 'running' | 'awaiting_review' | 'approved'
export interface PlanStep {
  id: string; kind: string; title: string; objective?: string
  status: PlanStepStatus
  artifact?: Record<string, unknown>
  comments?: { text: string; at: number }[]
}
export interface PlanSession {
  project_id: string; created_at: number; steps: PlanStep[]
  design_error?: string
}
export interface LoopPlannerStatus {
  active: boolean
  stalled: boolean
  retryable: boolean
  started_at: number | null
  last_activity_at: number | null
  server_time: number
  stall_after_seconds: number
}
export interface LoopPlanState {
  session: PlanSession | null
  planner: LoopPlannerStatus
}
export interface ChatPlanWire {
  session: PlanSession | null
  binding: { resume_task_mode?: string; parked?: boolean; parked_messages?: number }
  awaiting_step_id: string
  task_mode: TaskMode
}

export type ApprovalMode = 'normal' | 'trust' | 'trust_reads' | 'yolo'
export interface ApprovalScreeningVerdict {
  requested_mode: ApprovalMode
  requested_approval: 'ask' | 'hook_based' | 'auto' | string
  ceiling: 'open' | 'ask' | 'hook_based' | 'auto' | string
  verdict: 'allowed' | 'denied'
  reason: string
}
export interface ApprovalModeResult {
  ok: boolean
  mode: ApprovalMode
  approval_screening: ApprovalScreeningVerdict
}
export type TaskMode = 'agent' | 'ask' | 'plan' | 'build'
export type ReasoningEffort = '' | 'low' | 'medium' | 'high' | 'max'
export type MemoryMode = 'persistent' | 'incognito' | 'temporary'

export interface NudgeLoop {
  id: string; session_name: string; message: string; idle_secs: number
  max_cycles: number; cycle_count: number; active: boolean
  last_fire_ts: number; created_ts: number
}

export interface FsEntry { name: string; path: string; is_dir: boolean; size?: number; mtime?: number }
export interface FsRoot { label: string; path: string; name: string; is_dir: boolean }
export interface FileListResp { roots: FsRoot[]; entries: FsEntry[]; path: string }
export interface GitStatusResp { repoRoot: string; branch: string; statuses: Record<string, string> }
export interface ContentMatch { file: string; line: number; col: number; preview: string }
export interface ContentSearchResp { results: ContentMatch[]; engine: 'rg' | 'python'; truncated: boolean }

export type ArtifactKind = 'widget' | 'html' | 'react' | 'markdown' | 'svg' | 'json' | 'text' | 'infographic' | 'document' | 'image' | 'csv' | 'docx' | 'xlsx' | 'pptx' | 'pdf' | 'video'
export type ArtifactSource = 'chat' | 'cron' | 'subagent' | 'manual' | 'import'
export type TileSize = 's' | 'm' | 'l' | 'full'
export interface TileDataNode { id: string; provider: string; config: Record<string, unknown> }
export interface TileRefresh { mode: 'manual' | 'ttl'; ttl_secs: number; skeleton: string; data: TileDataNode[] }
export interface DashboardTile { ref: string; size: TileSize; order: number; added_by: 'user' | 'agent'; refresh: TileRefresh }
export interface DashboardView { id: string; name: string; icon?: string | null; nav_pinned: boolean; preset: boolean; tiles: DashboardTile[] }
export interface TileNodeOutcome { id: string; provider: string; ok: boolean; error: string; duration_ms: number }
export interface TileRefreshRow {
  kind?: string; event_id?: string; ts?: string; ok?: boolean
  tokens?: number; cost_usd?: number; duration_ms?: number
  nodes?: TileNodeOutcome[]; version?: number; rendered_bytes?: number; error?: string
}
export interface TileRefreshResult { refreshed: boolean; reason: string; ok: boolean; nodes: TileNodeOutcome[]; row: TileRefreshRow }
export interface SurfaceOverlayDefine { name: string; description: string; body: string }
export interface SurfaceOverlayDoc { file: string; surface: string; title: string; body: string; define: SurfaceOverlayDefine[] }
export interface SurfaceOverlayError { code: string; what: string; why: string; fix: string; suggestions: string[] }
export interface SurfaceOverlayRefusal { file: string; error: SurfaceOverlayError }
export interface SurfaceOverlayPayload { overlays: SurfaceOverlayDoc[]; refusals: SurfaceOverlayRefusal[]; dir: string }

export type ArtifactEventType = 'created' | 'edited' | 'iterated' | 'referenced' | 'reverted'
export interface ArtifactEvent {
  ts: string; type: ArtifactEventType; by: string; session_id: string
  version: number; from_version: number; metadata: Record<string, unknown>
}
export interface Artifact {
  slug: string; name: string; kind: ArtifactKind; source: ArtifactSource
  description: string; tags: string[]; version: number
  created_at: string; updated_at: string
  content?: string | null; events: ArtifactEvent[]
  source_path: string; live_dirty?: boolean; project_id?: string
  collection?: string
  readonly: boolean
}

export interface ArtifactUpdate {
  content?: string
  snapshot?: boolean
  event_type?: ArtifactEventType
  from_version?: number
  name?: string
  description?: string
  tags?: string[]
  collection?: string
}

export const ARTIFACT_MODEL_SAVED_EVENT = 'ne:artifact-model-saved'

function publishArtifactModelSaved<T extends { version: number }>(slug: string, previousVersion: number, result: T): T {
  if (!Number.isSafeInteger(result?.version) || result.version <= previousVersion) {
    throw new Error('The server did not confirm the saved version. Your edits are still marked unsaved.')
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(ARTIFACT_MODEL_SAVED_EVENT, {
      detail: { slug, version: result.version },
    }))
  }
  return result
}

export interface DocumentRun { text: string; bold: boolean; italic: boolean; code: boolean; link: string }
export interface DocumentParagraphStyle {
  align: string; space_before_pt: number; space_after_pt: number; line_spacing: number
  indent_left_pt: number; indent_right_pt: number; first_line_indent_pt: number
  keep_with_next: boolean
}
export interface DocumentCell { runs: DocumentRun[]; text: string; bold: boolean; align: string }
export interface DocumentPageSetup {
  size: string; orientation: string
  margin_top_pt: number; margin_bottom_pt: number
  margin_left_pt: number; margin_right_pt: number
  header_text: string; footer_text: string; page_numbers: boolean
}
export interface DocumentBlock {
  kind: 'heading' | 'paragraph' | 'bullets' | 'numbered' | 'table' | 'image' | 'pagebreak' | 'code'
  text: string; level: number; items: string[]; rows: string[][]
  artifact_slug: string; runs: DocumentRun[]; cells: DocumentCell[][]
  style: DocumentParagraphStyle | null
}
export interface DocumentModelJson { title: string; blocks: DocumentBlock[]; page: DocumentPageSetup | null }
export interface DocumentLossItem {
  kind: string; detail: string; where: string
  block_index: number; paragraph_ordinal: number
}
export interface DocumentLossReport {
  lossless: boolean; kinds: string[]; summary: string; items: DocumentLossItem[]
}
export interface DocumentModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: DocumentModelJson; loss: DocumentLossReport
}

export interface SheetCellJson {
  value: string | number | boolean | null
  formula: string
  number_format: string
  bold: boolean
  italic: boolean
  font_color: string
  fill: string
  align: string
}
export interface SheetJson {
  name: string
  cells: SheetCellJson[][]
  column_widths: number[]
  merges: string[]
  frozen_header: boolean
}
export interface SheetModelJson { sheets: SheetJson[] }
export interface SheetModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: SheetModelJson; loss: DocumentLossReport
}

export interface DeckBulletJson { text: string; level: number }
export interface DeckShapeBoxJson { left_in: number; top_in: number; width_in: number; height_in: number }
export interface DeckSlideJson {
  title: string
  bullets: DeckBulletJson[]
  notes: string
  artifact_slug: string
  layout: string
  title_box: DeckShapeBoxJson
  body_box: DeckShapeBoxJson
}
export interface DeckModelJson { title: string; slides: DeckSlideJson[]; width_in: number; height_in: number }
export interface DeckModelResponse {
  slug: string; kind: string; version: number; mime: string
  model: DeckModelJson; loss: DocumentLossReport
}

export interface ArtifactDeployment {
  slug: string
  entry: string
  created_at: string
  url: string
}

export interface UsageAgg {
  input_tokens: number; output_tokens: number
  cache_read_tokens: number; cache_creation_tokens: number
  cost_usd: number; turns: number; priced: boolean
}

export interface UsageFoldRow {
  key: string
  calls: number
  tokens_in: number; tokens_out: number; tokens: number
  dollars_est: number
  estimated_dollars: number
  estimated_share: number
  unpriced_calls: number
  local_calls: number
  priced: boolean
}

export interface UsageFold {
  window: string
  group: string
  dates: string[]
  rows: UsageFoldRow[]
  total: UsageFoldRow
  series: Array<{ date: string; calls: number; dollars_est: number; tokens: number }>
  estimated_share: number
  unmapped: Record<string, number>
  app_sources: Record<string, number>
  uncounted: {
    calls: number
    total_calls: number
    total_dollars_est: number
    by_use_case: Record<string, number>
  }
  reachable_purposes: string[]
}

export interface TelemetryRow {
  ref: string
  n: number
  success: number
  feedback: number
  avg_cost_usd: number
  p50_ms: number
  p95_ms: number
  on_frontier: boolean
}

export interface RoutingPolicyRow {
  use_case: string
  mode: 'off' | 'heuristic' | 'learned'
  pin: string
  candidates: Array<{ ref: string; local: boolean }>
  classes: Record<string, { order: string[]; basis: Record<string, unknown> }>
}

export interface RoutingProposal {
  id: string
  use_case: string
  query_class: string
  current: string[]
  proposed: string[]
  created_at: string
  status: string
  evidence: {
    n?: Record<string, number>
    scores?: Record<string, number>
    min_samples?: number
    hysteresis?: number
    cloud_quality_margin?: number
    p50_delta_ms?: number
    cost_delta_usd?: number
    sample_audit_ids?: string[]
  }
}

function _usageSessionKey(session: string): string {
  return session.includes(':') ? session : `dashboard:${session}`
}

function _usageQuery(opts?: { since?: string; until?: string; session?: string; group_by?: string }): string {
  const p = new URLSearchParams()
  if (opts?.group_by) p.set('group_by', opts.group_by)
  if (opts?.since) p.set('since', opts.since)
  if (opts?.until) p.set('until', opts.until)
  if (opts?.session) p.set('session', _usageSessionKey(opts.session))
  const q = p.toString()
  return q ? `?${q}` : ''
}

export interface InstalledPackRec {
  name: string
  version: string
  components: string[]
  connectors: Array<{ name: string; mode: string; server_name: string; marker: string; credentials_saved: string[]; error: string }>
  connector_markers: string[]
  setup_skill: string
  setup_pending: boolean
  installed_at: string
  pack_owned?: string[]
  component_locks?: Record<string, { source: string; computedHash: string; path: string }>
  roster?: Array<{ slug?: string; target?: string; tier?: string }>
  staged_triggers: string[]
}

export interface BundledPackRec {
  name: string
  version: string
  displayName: string
  description: string
}

export interface FingerprintMatchRec {
  label: string
  confidence: number
  declared_confidence: number
  matched_globs: string[]
  matched_signals: string[]
  declared_globs: string[]
  declared_signals: string[]
  evidence: string[]
}

export interface PackProposalRec {
  project_id: string
  pack: string
  displayName: string
  description: string
  version: string
  confidence: number
  matches: FingerprintMatchRec[]
  files_scanned: number
  inspect: { name: string; version: string; blocked: boolean; needs_consent: boolean; components: Array<{ kind: string; orig_id: string; target_id: string; verdict: string }>; requirements: unknown[]; staged_triggers: string[] } | null
  inspect_error: string
}

export interface PackUpdateRec {
  pack: string
  from_version: string
  to_version: string
  applied: boolean
  components: Array<{ ref: string; action: string; reason: string; pack_path: string; home_path: string }>
  drift_notes: string[]
  overwritten: string[]
  skipped: string[]
}

export interface RewindFileWire {
  path: string
  action: 'restore' | 'delete' | 'unchanged' | 'not_captured'
  turn: number
  reason: string
  current_size: number
  restored_size: number
  current_sha256: string
  restored_sha256: string
  diff: string
}
export interface RewindPreviewWire {
  session: string
  turn: number
  turns_affected: number[]
  warnings: string[]
  files: RewindFileWire[]
  notice?: string
}
export interface RewindApplyWire {
  ok: boolean
  turn: number
  restored: string[]
  deleted: string[]
  skipped: string[]
  errors: string[]
  safety_turn: number
  notice: string
  preview: RewindPreviewWire
}

export const api = {
  agentsInstalled: () => get<AgentDef[]>('/api/agents/installed'),
  savedAgents: () => get<{ agents: Array<{ name: string; description?: string; model?: string }> }>('/api/agents').then((d) => d.agents),
  agents: () => get<{ agents: SavedAgent[]; default_agent: string }>('/api/agents'),
  createAgent: (body: Record<string, unknown>) => post<{ ok: boolean }>('/api/agents', body),
  updateAgent: (name: string, body: Record<string, unknown>) => put<{ ok: boolean }>(`/api/agents/${encodeURIComponent(name)}`, body),
  deleteAgent: (name: string) => del(`/api/agents/${encodeURIComponent(name)}`),
  setDefaultAgent: (name: string) => put<{ ok: boolean; default_agent: string }>('/api/config/default-agent', { agent: name }),
  routingDismiss: (agent: string) => post<{ ok: boolean; count: number; muted: boolean }>('/api/agents/routing/dismiss', { agent }),
  routingUnmute: (agent: string) => post<{ ok: boolean; agent: string }>('/api/agents/routing/unmute', { agent }),
  routingStatus: () => get<{ enabled: boolean; muted: string[]; dismissals: Record<string, { count: number; last_dismissed_at: number }> }>('/api/agents/routing/status'),
  usageTotals: (opts?: { session?: string; since?: string; until?: string }) => get<{ session: string; totals: UsageAgg }>(`/api/usage/totals${_usageQuery(opts)}`),
  usageRollup: (opts?: { group_by?: 'model' | 'source' | 'agent' | 'provider' | 'day'; since?: string; until?: string; session?: string }) => get<{ group_by: string; rows: Array<UsageAgg & Record<string, string>> }>(`/api/usage/rollup${_usageQuery(opts)}`),
  usageFold: (opts?: { window?: 'day' | 'week' | 'month'; group?: 'model' | 'provider' | 'purpose' }) => {
    const p = new URLSearchParams()
    if (opts?.window) p.set('window', opts.window)
    if (opts?.group) p.set('group', opts.group)
    const q = p.toString()
    return get<UsageFold>(`/api/usage${q ? `?${q}` : ''}`)
  },
  modelsTelemetry: (opts: { use_case: string; query_class: string }) =>
    get<{ use_case: string; query_class: string; rows: TelemetryRow[] }>(
      `/api/models/telemetry?use_case=${encodeURIComponent(opts.use_case)}&query_class=${encodeURIComponent(opts.query_class)}`,
    ),
  routingPolicy: () =>
    get<{ enabled: boolean; use_cases: RoutingPolicyRow[] }>('/api/models/routing-policy'),
  setRoutingPolicy: (body: {
    use_case: string
    mode?: 'off' | 'heuristic' | 'learned'
    pin?: string
    query_class?: string
    order?: string[]
  }) => put<{ ok: boolean; use_case: string; applied: string[] }>('/api/models/routing-policy', body),
  routingProposals: () =>
    get<{ count: number; proposals: RoutingProposal[] }>('/api/models/routing-proposals'),
  acceptRoutingProposal: (id: string) =>
    post<{ ok: boolean; applied: boolean; id: string; reason?: string }>(
      `/api/models/routing-proposals/${encodeURIComponent(id)}/accept`,
      {},
    ),
  rejectRoutingProposal: (id: string) => del(`/api/models/routing-proposals/${encodeURIComponent(id)}`),
  gideonConfig: () => get<Record<string, any>>('/api/config/gideon'),
  settingsConfig: () => get<{ sections: { id: string; label: string; path: string; field_label: string; value: boolean }[] }>('/api/config/settings'),
  patchConfig: (path: string, value: unknown) => patch<Record<string, any>>('/api/config/gideon', { path, value }),

  companionDiscovery: () => get<CompanionDiscovery>('/api/companion/discovery'),
  devices: () => get<{ devices: DeviceRec[] }>('/api/devices').then((d) => d.devices),
  devicePairStart: (label?: string) =>
    post<DevicePairStart>('/api/devices/pair/start', label ? { label } : {}),
  deviceRevoke: (id: string) =>
    post<{ ok: boolean; revoked: number }>(`/api/devices/${encodeURIComponent(id)}/revoke`, {}),

  packsInstalled: () => get<{ packs: InstalledPackRec[] }>('/api/packs/installed').then((d) => d.packs),
  packFinishSetup: (name: string) => post<{ pack: string; setup_skill: string; command: string; pending: boolean }>(`/api/packs/${encodeURIComponent(name)}/finish-setup`, {}),
  packsBundled: () => get<{ packs: BundledPackRec[] }>('/api/packs/bundled').then((d) => d.packs),
  packBundledInstall: (name: string) => post<{ ok: boolean; plan: Record<string, unknown> }>(`/api/packs/bundled/${encodeURIComponent(name)}/install`, {}),
  packProposals: (projectId?: string) => get<{ proposals: PackProposalRec[] }>(`/api/packs/proposals${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`).then((d) => d.proposals),
  packRejectProposal: (projectId: string, pack: string) => post<{ ok: boolean }>('/api/packs/proposals/reject', { project_id: projectId, pack }),
  packUpdate: (name: string, confirm = false) => post<{ ok: boolean; update: PackUpdateRec }>(`/api/packs/${encodeURIComponent(name)}/update`, { confirm }),
  packRosterDeploy: (name: string) => post<{ ok: boolean; pack: string; deployed: string[]; dormant: string[]; missing: string[] }>(`/api/packs/${encodeURIComponent(name)}/roster/deploy`, {}),
  packTriggersDeploy: (name: string) => post<{ ok: boolean; pack: string; deployed: string[]; skipped: Array<{ file: string; id?: string; reason: string }> }>(`/api/packs/${encodeURIComponent(name)}/triggers/deploy`, {}),

  authSession: () => get<{
    login_enabled: boolean
    credential_configured: boolean
    username: string
    totp_enabled: boolean
    totp_required: boolean
    session_ttl: string
    lockout_threshold: number
    lockout_window: string
    user: string
  }>('/api/auth/session'),
  setLoginPassword: (username: string, password: string) =>
    post<{ ok: boolean; username: string }>('/api/auth/password', { username, password }),
  authLogout: () => post<{ ok: boolean; revoked: boolean }>('/api/auth/logout'),

  externalAccess: () => get<ExternalAccess>('/api/external-access'),
  externalAccessCreateClient: (body: {
    label: string
    surfaces: string[]
    agent?: string
    tools?: string[]
    scope?: Record<string, unknown>
    rate_overrides?: Record<string, unknown>
  }) =>
    post<{
      ok: boolean
      client_id: string
      label: string
      surfaces: string[]
      token: string
      token_notice: string
    }>('/api/external-access/clients', body),
  externalAccessRevokeClient: (clientId: string) =>
    del(`/api/external-access/clients/${encodeURIComponent(clientId)}`),
  externalAccessSetClientDisabled: (clientId: string, disabled: boolean) =>
    post<{ ok: boolean; client_id: string; disabled: boolean }>(
      `/api/external-access/clients/${encodeURIComponent(clientId)}/disabled`,
      { disabled },
    ),

  incident: () => get<{ active: boolean; reason: string; started_at: string }>('/api/incident'),
  incidentOn: (reason: string) =>
    post<{ active: boolean; reason: string; started_at: string }>('/api/incident', { reason }),
  incidentResume: () => post<{ active: boolean }>('/api/incident/resume', { confirm: true }),
  modelsHealth: () =>
    get<{ providers: ProviderHealth[]; callers?: CallerHealth[]; generated_from: number }>(
      '/api/models/health',
    ),

  autonomyLadder: () => get<AutonomyLadder>('/api/autonomy'),
  autonomyGrant: (key: string, rung: string) =>
    post<{ ok: boolean; key: string; rung: string; evidence: string }>('/api/autonomy/grant', { key, rung }),
  autonomyDemote: (key: string) =>
    post<{ ok: boolean; key: string; cooldown_until: string }>('/api/autonomy/demote', { key }),
  autonomyUndo: (id: string) =>
    post<{ ok: boolean; code: string; action_type: string; demoted: boolean; detail?: string }>('/api/autonomy/undo', { id }),

  doctor: () => get<DoctorReport>('/api/doctor'),
  doctorCapability: (capability: string) =>
    get<{ capability: string; ok: boolean; probes: DoctorProbe[]; unknown?: boolean }>(
      `/api/doctor/${encodeURIComponent(capability)}`,
    ),
  degraded: () => get<DegradedReport>('/api/resilience/degraded'),
  durabilityStatus: () => get<DurabilityStatus>('/api/durability/status'),
  durabilityRun: (job: 'export' | 'snapshot' | 'drill') =>
    post<DurabilityJobResult>('/api/durability/run', { job }),
  durabilityArchive: () => get<DurabilityArchives>('/api/durability/archive'),
  durabilityExport: (domains?: string[]) =>
    fetch('/api/durability/export', {
      method: 'POST',
      headers: { ...SK, 'Content-Type': 'application/json' },
      body: JSON.stringify(domains && domains.length ? { domains } : {}),
    }).then(async (r) => {
      if (!r.ok) throw await apiError(r)
      return r.blob()
    }),
  durabilityImport: (file: File, mode?: 'merge' | 'replace') => {
    const fd = new FormData(); fd.append('file', file)
    const qs = mode ? `?mode=${mode}${mode === 'replace' ? '&confirm=true' : ''}` : ''
    return fetch(`/api/durability/import${qs}`, { method: 'POST', headers: { ...SK }, body: fd })
      .then(j<DurabilityImportResult>)
  },
  durabilityArchiveRestore: (id: string, body: { mode?: 'merge' | 'replace'; components?: string[]; confirm?: boolean } = {}) =>
    post<DurabilityRestoreResult>(`/api/durability/archive/${encodeURIComponent(id)}/restore`, body),
  durabilityConflicts: (surface?: string, status?: string) => {
    const qs = new URLSearchParams()
    if (surface) qs.set('surface', surface)
    if (status) qs.set('status', status)
    const q = qs.toString()
    return get<DurabilityConflicts>(`/api/durability/conflicts${q ? `?${q}` : ''}`)
  },
  durabilityHistory: () => get<DurabilityHistoryStatus>('/api/durability/history'),
  durabilityHistoryTimeline: (root: string, opts: { limit?: number; unattended?: boolean } = {}) => {
    const q = new URLSearchParams()
    if (opts.limit) q.set('limit', String(opts.limit))
    if (opts.unattended) q.set('unattended', '1')
    const qs = q.toString()
    return get<DurabilityHistoryTimeline>(
      `/api/durability/history/${encodeURIComponent(root)}/timeline${qs ? `?${qs}` : ''}`,
    )
  },
  durabilityHistoryPreview: (
    root: string, op: 'rollback' | 'revert', sha: string, paths?: string[],
  ) =>
    post<DurabilityHistoryPreviewResponse>(
      `/api/durability/history/${encodeURIComponent(root)}/${op}`,
      { sha, ...(paths?.length ? { paths } : {}) },
    ),
  durabilityHistoryApply: (
    root: string, op: 'rollback' | 'revert', sha: string, expectedHead: string, paths?: string[],
  ) =>
    post<DurabilityHistoryResult>(
      `/api/durability/history/${encodeURIComponent(root)}/${op}`,
      { sha, confirm: true, expected_head: expectedHead, ...(paths?.length ? { paths } : {}) },
    ),
  resolveDurabilityConflict: (id: string, choice: DurabilityConflictChoice) =>
    post<{ ok: boolean; choice: string; id: string; written: number; removed: number; conflict: DurabilityConflict }>(
      `/api/durability/conflicts/${encodeURIComponent(id)}/resolve`, { choice, confirm: true },
    ),
  doctorFixes: () => get<{ fixes: DoctorFix[] }>('/api/doctor/fixes'),
  doctorFixApply: (fixId: string) =>
    post<{ ok: boolean; fix_id: string; result?: string; error?: string }>(
      `/api/doctor/fix/${encodeURIComponent(fixId)}`, { confirm: true },
    ),
  doctorSimulateSurfacing: (text: string) =>
    post<{ query: string; candidates: SurfacingCandidate[] }>(
      '/api/doctor/simulate/surfacing', { text },
    ),
  doctorSimulateAutomation: (triggerId: string) =>
    post<AutomationWouldExecute>('/api/doctor/simulate/automation', { trigger_id: triggerId }),
  doctorCrash: (filename: string) =>
    get<Record<string, unknown>>(`/api/doctor/crash/${encodeURIComponent(filename)}`),
  doctorRemediation: () => get<RemediationSnapshot>('/api/doctor/remediation'),
  doctorRemediationRun: () =>
    post<{ score_before: number; score_after: number; jobs: RemediationJobRow[]; stopped_reason: string }>(
      '/api/doctor/remediation/run', { confirm: true },
    ),

  memoryGraph: () => get<MemoryGraphData>('/api/memory/graph'),
  memoryLint: () => get<MemoryLint>('/api/memory/lint'),
  memoryObservability: () => get<MemoryObservability>('/api/memory/observability'),
  memoryRecall: (q: string) => get<MemoryRecallResult>(`/api/memory/recall?q=${encodeURIComponent(q)}`),
  memoryPromote: () => post<{ ok: boolean; promoted: number }>('/api/memory/promote'),
  memoryEntities: () => get<MemoryEntitiesResponse>('/api/memory/entities'),
  memoryEntityCreate: (body: { name: string; entity_type: MemoryEntityType; aliases?: string[] }) =>
    post<{ ok: boolean; id: string }>('/api/memory/entities', body),
  memoryEntityBacklinks: (id: string) =>
    get<{ links: MemoryLink[] }>(`/api/memory/entities/${encodeURIComponent(id)}/backlinks`),
  memoryEntityProposal: (body: { name: string; action: 'accept' | 'reject'; entity_type?: MemoryEntityType }) =>
    post<{ ok: boolean; id?: string }>('/api/memory/entities/proposals', body),
  memoryEntityProposals: () =>
    get<{ proposals: MemoryEntityProposal[]; enabled: boolean }>('/api/memory/entities/proposals'),
  memoryEntityGraph: () => get<MemoryEntityGraph>('/api/memory/graph/entities'),
  memoryRecordLinks: (ref: string) =>
    get<{ links: MemoryRecordLink[]; ref: string; enabled: boolean }>(
      `/api/memory/record-links?ref=${encodeURIComponent(ref)}`),
  memoryGraphExport: () =>
    fetch('/api/memory/graph/export', { headers: { ...SK } }).then(async (r) => {
      if (!r.ok) throw await apiError(r)
      return r.text()
    }),
  memoryPinFacet: (key: string, pinned: boolean) => post<{ ok: boolean }>('/api/memory/facets', { key, action: 'pin', pinned }),
  memoryForgetFacet: (key: string) => post<{ ok: boolean }>('/api/memory/facets', { key, action: 'forget' }),
  memorySlots: () => get<MemorySlotsResponse>('/api/memory/slots'),
  memorySlotAppend: async (name: string, text: string): Promise<MemorySlotAppendResult> => {
    const r = await fetch(`/api/memory/slots/${encodeURIComponent(name)}/lines`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ text }),
    })
    const data = await r.json().catch(() => null)
    if (data && typeof data === 'object') return data as MemorySlotAppendResult
    return { ok: false, error: `HTTP ${r.status}` }
  },
  memorySlotRetireLine: (name: string, text: string) =>
    post<{ ok: boolean }>(`/api/memory/slots/${encodeURIComponent(name)}/lines/retire`, { text }),
  memoryGraphRebuild: () => post<MemoryGraphRebuild>('/api/memory/graph/rebuild'),
  memoryDoc: (which: 'preferences' | 'projects' | 'history') => get<{ content: string }>(`/api/memory/${which}`).then((d) => d.content),
  saveMemoryDoc: (which: 'preferences' | 'projects' | 'history', content: string) => put<{ ok: boolean }>(`/api/memory/${which}`, { content }),
  memoryMigrate: () => post<Record<string, number>>('/api/memory/migrate'),
  memoryImport: (data: unknown) => post<Record<string, number>>('/api/memory/import', data),
  lessons: () => get<{ lessons: Lesson[] }>('/api/lessons').then((d) => d.lessons),
  addLesson: (rule: string, category = 'knowledge') => post<{ ok: boolean }>('/api/lessons', { rule, category }),
  deleteLesson: (rule: string) => fetch('/api/lessons', { method: 'DELETE', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ rule }) }).then(j<{ ok: boolean }>),

  sessionsSearch: (q: string) => get<{ sessions: Array<{ key: string; title?: string; messages?: number; snippet?: string }>; source?: string }>(`/api/sessions/search?q=${encodeURIComponent(q)}`).then((d) => d.sessions),

  spawnedAgents: () => get<{ agents: SpawnedAgent[] }>('/api/spawn').then((d) => d.agents),
  cancelSpawnedAgent: (id: string) => del(`/api/spawn/${encodeURIComponent(id)}`),
  clearSpawnedAgents: () => del('/api/spawn'),
  cancelFanout: (parentSession: string) =>
    post<{ ok: boolean; cancelled: number }>('/api/spawn/cancel-fanout', { parent_session: parentSession }),

  knowledgeSearchForContext: (q: string, maxTokens = 4000) =>
    get<KnowledgeContextResult>(`/api/knowledge/search-for-context?q=${encodeURIComponent(q)}&max_tokens=${maxTokens}`),

  agentMetadata: (name: string) => get<{ name: string; content: string }>(`/api/agent-metadata/${encodeURIComponent(name)}`).then((d) => d.content),
  saveAgentMetadata: (name: string, content: string) => put<{ ok: boolean }>(`/api/agent-metadata/${encodeURIComponent(name)}`, { content }),
  mcpActive: (agent?: string) => get<McpActiveServer[]>(`/api/mcp/active${agent ? `?agent=${encodeURIComponent(agent)}` : ''}`),
  agentHooks: () => get<{ hooks: Record<string, AgentHook[]> }>('/api/agent-hooks').then((d) => d.hooks),
  syncAgents: () => post<{ ok: boolean; synced?: number }>('/api/agents/sync'),

  channels: () => get<{ channels: ChannelRuntime[] }>('/api/channels').then((d) => d.channels),
  connectChannel: (name: string) => post<{ ok: boolean; health?: ChannelHealth }>(`/api/channels/${encodeURIComponent(name)}/connect`),
  disconnectChannel: (name: string) => post<{ ok: boolean }>(`/api/channels/${encodeURIComponent(name)}/disconnect`),
  testChannel: (name: string) => post<{ ok: boolean; health?: ChannelHealth; detail?: string }>(`/api/channels/${encodeURIComponent(name)}/test`),

  channelTrust: () => get<ChannelTrust>('/api/channels/trust'),
  revokeChannelSender: (provider: string, senderId: string) =>
    del(`/api/channels/trust/${encodeURIComponent(provider)}/senders/${encodeURIComponent(senderId)}`),

  tasksBulk: (op: 'create' | 'update' | 'delete', items: Array<Record<string, unknown>>) =>
    post<{ total: number; succeeded: number; failed: number; results?: unknown[]; errors?: unknown[] }>('/api/tasks/bulk', { op, items }),


  briefSession: (key: string, content: string, source = 'user-brief') =>
    post<{ ok: boolean }>(`/api/chat/sessions/${encodeURIComponent(key)}/context`, { content, source, ephemeral: false }),
  setSessionWorkspaceDir: (key: string, workspace_dir: string) =>
    post<{ ok: boolean; workspace_dir?: string }>(`/api/chat/sessions/${encodeURIComponent(key)}/workspace-dir`, { workspace_dir }),

  suggestions: (force = false) => get<{ suggestions: string[]; generated_at: number; stale: boolean }>(`/api/suggestions${force ? '?force=1' : ''}`),

  discover: () => get<DiscoverResponse>('/api/legibility/discover'),
  dismissDiscoverTip: (id: string) => post<{ ok: boolean; dismissed: string[] }>('/api/legibility/discover/dismiss', { id }),
  clearDismissedDiscoverTips: () => del('/api/legibility/discover/dismiss'),

  alwaysOn: (projectId = '') =>
    get<AlwaysOnResponse>(`/api/legibility/always-on${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  alwaysOnDoc: (id: string, projectId = '') =>
    get<AlwaysOnItem>(`/api/legibility/always-on/doc?id=${encodeURIComponent(id)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''}`),
  saveAlwaysOnDoc: (id: string, projectId: string, body: string) =>
    put<{ ok: boolean; item: AlwaysOnItem }>('/api/legibility/always-on/doc', { id, project_id: projectId, body }),

  revealPath: (path: string, action: 'reveal' | 'open' = 'reveal') =>
    post<{ ok: boolean; copy?: string }>('/api/reveal', { path, action }),
  screenshot: () => post<{ path: string; error?: string }>('/api/screenshot'),

  logsUrl: (lines = 200) => `/api/logs?lines=${encodeURIComponent(String(lines))}`,
  logLevel: () => get<{ level: string }>('/api/logs/level').then((d) => d.level),
  setLogLevel: (level: string) => post<{ ok: boolean; level: string; persisted: boolean }>('/api/logs/level', { level }),

  themes: () => get<{ themes: ThemeSummary[] }>('/api/themes').then((d) => d.themes),
  theme: (slug: string) => get<ThemeRecord>(`/api/themes/${encodeURIComponent(slug)}`),
  createTheme: (body: ThemeWrite) => post<{ ok: boolean; slug: string; theme: ThemeRecord }>('/api/themes', body),
  updateTheme: (slug: string, body: ThemeWrite) => put<{ ok: boolean; theme: ThemeRecord }>(`/api/themes/${encodeURIComponent(slug)}`, body),
  deleteTheme: (slug: string) => del(`/api/themes/${encodeURIComponent(slug)}`),
  agentProviders: () => get<{ agent_providers: AgentProvider[] }>('/api/agent-providers').then((d) => d.agent_providers),
  agentProviderAgents: (id: string, refresh = false) =>
    get<{ agents: DiscoveredAgent[]; permission_modes: string[] }>(`/api/agent-providers/${encodeURIComponent(id)}/agents${refresh ? '?refresh=1' : ''}`),

  models: () => get<ModelItem[]>('/api/models/chat'),
  settingsProviders: () => get<{ providers: SettingsProvider[] }>('/api/providers').then((d) => d.providers),
  providerSchema: (name: string) => get<{ schema: ProviderSchema }>(`/api/providers/${encodeURIComponent(name)}/schema`).then((d) => d.schema),
  providerConfig: (name: string) => get<{ config: Record<string, unknown>; _secret_set?: string[] }>(`/api/providers/${encodeURIComponent(name)}/config`),
  saveProviderConfig: (name: string, config: Record<string, unknown>) =>
    patch<{ config: Record<string, unknown> }>(`/api/providers/${encodeURIComponent(name)}/config`, config),
  enableProvider: (name: string) => post<{ enabled: boolean }>(`/api/providers/${encodeURIComponent(name)}/enable`),
  disableProvider: (name: string) => post<{ enabled: boolean }>(`/api/providers/${encodeURIComponent(name)}/disable`),
  agentRuntimes: (refresh = false) => get<{ agent_providers: AgentRuntime[] }>(`/api/agent-providers${refresh ? '?refresh=1' : ''}`).then((d) => d.agent_providers),
  agentRunners: (probe = false) => get<{ runners: RunnerRow[] }>(`/api/agent-runners${probe ? '?probe=1' : ''}`).then((d) => d.runners),
  providerInstances: (name: string) => get<{ instances: ProviderInstance[] }>(`/api/providers/${encodeURIComponent(name)}/instances`).then((d) => d.instances),
  createProviderInstance: (name: string, body: { display_name: string; config: Record<string, unknown> }) =>
    post<{ instance: ProviderInstance }>(`/api/providers/${encodeURIComponent(name)}/instances`, body),
  updateProviderInstance: (name: string, id: string, body: { display_name?: string; config?: Record<string, unknown>; enabled?: boolean }) =>
    put<{ instance: ProviderInstance }>(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}`, body),
  deleteProviderInstance: (name: string, id: string) => del(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}`),
  testProviderInstance: (name: string, id: string) => post<ProviderTestResult>(`/api/providers/${encodeURIComponent(name)}/instances/${encodeURIComponent(id)}/test`),
  modelProviders: () => get<{ providers: ModelProvider[] }>('/api/model-providers').then((d) => d.providers),
  modelProviderTypes: () => get<{ types: ModelProviderType[] }>('/api/model-provider-types').then((d) => d.types),
  createModelProvider: (body: { name: string; type: string; model?: string; options?: Record<string, string> }) =>
    post<{ ok: boolean; name: string }>('/api/model-providers', body),
  updateModelProvider: (name: string, body: { model?: string; type?: string; options?: Record<string, string> }) =>
    put<{ ok: boolean }>(`/api/model-providers/${encodeURIComponent(name)}`, body),
  deleteModelProvider: (name: string) => del(`/api/model-providers/${encodeURIComponent(name)}`),
  testModelProvider: (name: string) => post<ProviderTestResult>(`/api/model-providers/${encodeURIComponent(name)}/test`),
  localModelTokenStatus: () => get<LocalModelTokenStatus>('/api/models/huggingface/auth'),
  saveLocalModelToken: (token: string) => put<LocalModelTokenStatus>('/api/models/huggingface/auth', { token }),
  deleteLocalModelToken: () => del('/api/models/huggingface/auth'),
  testLocalModelToken: () => post<LocalModelTokenStatus>('/api/models/huggingface/auth/test'),
  testLocalModelProvider: async (provider: string) => {
    const response = await gatewayRequest(`/api/models/local/${encodeURIComponent(provider)}/selftest`, 'POST')
    const errorResponse = response.clone()
    const result = await response.json().catch(() => null) as LocalModelSelfTestResult | null
    if (result && typeof result === 'object' && Array.isArray(result.tests)) return result
    if (!response.ok) throw await apiError(errorResponse)
    throw new Error('Local model self-test returned an invalid response')
  },
  modelsAvailable: () => get<AvailableModelsResponse>('/api/models/available').then((d) => {
    const hostFit = d.fit
    if (!hostFit) return d.providers
    return d.providers.map((p) => ({
      ...p, host_fit: hostFit,
      models: p.models ? p.models.map((m) => ({ ...m, host_fit: hostFit })) : p.models,
    }))
  }),
  modelsActive: () => get<{ use_cases: Record<string, string[]> }>('/api/models/active').then((d) => d.use_cases),
  searchProviders: () => get<{ providers: SearchProviderInfo[] }>('/api/search/providers').then((d) => d.providers),
  searchActive: () => get<{ use_cases: Record<string, string[]> }>('/api/search/active').then((d) => d.use_cases),
  setActiveSearchProvider: (useCase: string, providers: string[]) => put<{ ok?: boolean }>(`/api/search/active/${encodeURIComponent(useCase)}`, { providers }),
  ollamaModels: (provider: string) =>
    get<{ models: OllamaLocalModel[]; error?: string }>(`/api/model-providers/${encodeURIComponent(provider)}/models`),
  ollamaSearch: (provider: string, q: string) =>
    get<{ results: OllamaSearchResult[]; error?: string }>(`/api/model-providers/${encodeURIComponent(provider)}/search?q=${encodeURIComponent(q)}`),
  ollamaShow: (provider: string, model: string) =>
    get<OllamaModelInfo>(`/api/model-providers/${encodeURIComponent(provider)}/show?model=${encodeURIComponent(model)}`),
  ollamaDeleteModel: (provider: string, model: string) =>
    post<{ ok: boolean; model: string }>(`/api/model-providers/${encodeURIComponent(provider)}/models/delete`, { model }),
  pullOllamaModel: async (provider: string, model: string, onFrame: (f: Record<string, unknown>) => void, signal?: AbortSignal) => {
    const r = await fetch(`/api/model-providers/${encodeURIComponent(provider)}/pull`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ model }), signal,
    })
    if (!r.ok || !r.body) throw new Error(await errText(r))
    const reader = r.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl: number
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim()
        buf = buf.slice(nl + 1)
        if (line) { try { onFrame(JSON.parse(line)) } catch {   } }
      }
    }
    if (buf.trim()) { try { onFrame(JSON.parse(buf.trim())) } catch {   } }
  },
  startModelDownload: (provider: string, model: string) =>
    post<DownloadJob>('/api/models/downloads', { provider, model }),
  modelDownloads: () => get<{ downloads: DownloadJob[] }>('/api/models/downloads').then((d) => d.downloads ?? []),
  cancelModelDownload: (id: string) => del(`/api/models/downloads/${encodeURIComponent(id)}`),
  downloadStreamUrl: (id: string) => `/api/models/downloads/${encodeURIComponent(id)}/stream`,
  modelDownloadCleanupCandidates: () =>
    get<{ candidates: { path: string; bytes: number }[]; total_bytes: number }>('/api/models/downloads/cleanup-candidates'),
  modelDownloadCleanup: () =>
    post<{ removed: number; freed_bytes: number }>('/api/models/downloads/cleanup', { confirm: true }),
  deleteLocalModel: (provider: string, model: string) =>
    del(`/api/models/local/${encodeURIComponent(provider)}/${encodeURIComponent(model)}`),
  modelsLoaded: () => get<ResidencySnapshot>('/api/models/loaded'),
  unloadModelProvider: (provider: string) =>
    post<{ ok: boolean; provider: string; kind: string; freed: boolean; pressure: MemoryPressure }>(
      '/api/models/unload', { provider }),
  sidecarInstallStatus: (provider: string) =>
    get<{
      provider: string; installed: boolean; managed: boolean; install_dir: string
      job: {
        state: string; progress: number
        steps: { name: string; status: string; detail: string }[]
        log_tail: string[]; error: string; reason: string; remediation: string
        weights_progress: number
      }
    }>(`/api/models/sidecar/${encodeURIComponent(provider)}/install/status`),
  startSidecarInstall: (provider: string) =>
    post<DownloadJob>(`/api/models/sidecar/${encodeURIComponent(provider)}/install`),
  deleteSidecarInstall: (provider: string) =>
    del(`/api/models/sidecar/${encodeURIComponent(provider)}/install`),
  searchLocalModels: (provider: string, q: string) =>
    get<{ models: LocalModel[] }>(`/api/models/local/${encodeURIComponent(provider)}/search?q=${encodeURIComponent(q)}`).then((d) => d.models ?? []),
  dashboardConfig: () => get<DashboardConfig>('/api/dashboard/config'),
  saveDashboardConfig: (body: Partial<DashboardConfig>) => put<{ ok: boolean }>('/api/dashboard/config', body),

  screenShareState: (session: string) =>
    get<{ enabled: boolean; delivery: 'native' | 'described' | 'none'; reason: string; staged: boolean }>(`/api/chat/screen-frame?session=${encodeURIComponent(session)}`),
  screenShareSignal: (session: string, action: 'start' | 'stop') =>
    post<{ ok: boolean; sharing: boolean }>('/api/chat/screen-frame', { session, action }),
  stageScreenFrame: (session: string, frame_b64: string) =>
    post<{ ok: boolean; staged: boolean }>('/api/chat/screen-frame', { session, action: 'frame', frame_b64 }),
  pinScreenFrame: (session: string, frame_b64: string) =>
    post<{ ok: boolean; path: string; name: string }>('/api/chat/screen-frame/pin', { session, frame_b64 }),

  onboarding: () => get<OnboardingState>('/api/onboarding'),
  saveOnboardingState: (patch: OnboardingStatePatch) =>
    post<{ ok: boolean; state: OnboardingState }>('/api/onboarding/state', patch),
  onboardingImportScan: () => get<OnboardingImportScan>('/api/onboarding/import'),
  runOnboardingImport: (body: { sources: string[]; categories: string[] }) =>
    post<OnboardingImportReport>('/api/onboarding/import', body),
  chatModels: () => get<ChatModelOption[]>('/api/models/chat'),
  setActiveModel: (useCase: string, models: string[]) => put<{ ok?: boolean }>(`/api/models/active/${encodeURIComponent(useCase)}`, { models }),
  startEmbeddingReindex: () => post<ReindexJob>('/api/models/embedding/reindex'),
  embeddingReindexStreamUrl: (id: string) => `/api/models/embedding/reindex/${encodeURIComponent(id)}/stream`,

  slashCommands: () => get<{ name: string; description: string }[]>('/api/slash-commands'),
  chatSessions: (archived = false) =>
    get<ChatSessionSummary[]>(`/api/chat/sessions${archived ? '?archived=1' : ''}`),
  pinChatSession: (session: string, pinned: boolean) => patch(`/api/chat/sessions/${encodeURIComponent(session)}/pin`, { pinned }),
  chatFolders: () => get<ChatFolder[]>('/api/chat/folders'),
  createChatFolder: (name: string, parentId?: string) => post<ChatFolder>('/api/chat/folders', { name, parent_id: parentId || '' }),
  updateChatFolder: (id: string, body: Partial<ChatFolder>) => patch<ChatFolder>(`/api/chat/folders/${encodeURIComponent(id)}`, body),
  deleteChatFolder: (id: string) => del(`/api/chat/folders/${encodeURIComponent(id)}`),
  setSessionFolder: (session: string, folderId: string | null) => patch(`/api/chat/sessions/${encodeURIComponent(session)}/folder`, { folder_id: folderId || '' }),
  chatTags: () => get<ChatTag[]>('/api/chat/tags'),
  createChatTag: (name: string, color?: string) => post<ChatTag>('/api/chat/tags', { name, color: color || '' }),
  updateChatTag: (id: string, body: Partial<ChatTag>) => patch<ChatTag>(`/api/chat/tags/${encodeURIComponent(id)}`, body),
  deleteChatTag: (id: string) => del(`/api/chat/tags/${encodeURIComponent(id)}`),
  setSessionTags: (session: string, tags: string[]) => put(`/api/chat/sessions/${encodeURIComponent(session)}/tags`, { tags }),
  organizeSuggestion: (session: string, opts: { llm?: boolean } = {}) =>
    get<{ proposal: OrganizeProposal | null }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize${opts.llm === false ? '?llm=0' : ''}`),
  organizeAccept: (session: string, p: OrganizeProposal) =>
    post<{ ok: boolean; folder_id: string; tags: string[] }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize/accept`, { folder_id: p.folder_id, folder_name: p.folder_name, tags: p.tags, source: p.source }),
  organizeDecline: (session: string, p: OrganizeProposal) =>
    post<{ ok: boolean; declined: boolean }>(`/api/chat/sessions/${encodeURIComponent(session)}/organize/decline`, { folder_id: p.folder_id, folder_name: p.folder_name, tags: p.tags, source: p.source }),
  retagAllSessions: () => post<RetagJob>('/api/sessions/retag-all', {}),
  retagStatus: () => get<RetagJob>('/api/sessions/retag-all'),
  cancelRetag: () => post('/api/sessions/retag-all/cancel', {}),
  tagColumns: () => get<TagColumn[]>('/api/chat/tag-columns'),
  createTagColumn: (body: Partial<TagColumn>) => post<TagColumn>('/api/chat/tag-columns', body),
  updateTagColumn: (id: string, body: Partial<TagColumn>) => patch<TagColumn>(`/api/chat/tag-columns/${encodeURIComponent(id)}`, body),
  deleteTagColumn: (id: string) => del(`/api/chat/tag-columns/${encodeURIComponent(id)}`),
  reorderTagColumns: (ids: string[]) => put('/api/chat/tag-columns/order', { ids }),
  dropSessionToColumn: (session: string, columnId: string) => post(`/api/chat/sessions/${encodeURIComponent(session)}/drop`, { column_id: columnId }),
  chatSessionDetail: (key: string) => get<{ key: string; title: string; messages: ChatHistoryMsg[]; running?: boolean; pending_approval?: boolean; agent?: string; model?: string; mode?: string; acp_provider?: string; acp_provider_agent?: string; reasoning_effort?: string; task_mode?: TaskMode; approval?: ApprovalMode; memory_mode?: string; queue?: { id: string; content: string }[]; side?: { open: boolean; messages: { role: string; content: string }[] } | null
    forked_from?: string; forked_from_title?: string
    natural_voice?: string; natural_voice_agent_default?: boolean
    natural_voice_effective?: boolean; natural_voice_source?: string }>(`/api/chat/sessions/${encodeURIComponent(key)}`),
  deleteChatSession: (key: string) => del(`/api/chat/sessions/${encodeURIComponent(key)}`),
  setSessionNaturalVoice: (session: string, choice: '' | 'on' | 'off') =>
    patch<{ ok: boolean; natural_voice: string; natural_voice_agent_default: boolean; natural_voice_effective: boolean; natural_voice_source: string }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/natural-voice`, { natural_voice: choice }),
  setSessionLifecycle: (session: string, body: { lifecycle?: 'active' | 'archived'; never_archive?: boolean }) =>
    patch<{ ok: boolean; lifecycle: string; never_archive: boolean }>(`/api/chat/sessions/${encodeURIComponent(session)}/lifecycle`, body),
  bulkSessions: (op: 'archive' | 'restore' | 'tag' | 'untag' | 'folder' | 'never_archive', keys: string[], args: { tag_id?: string; folder_id?: string; value?: boolean } = {}) =>
    post<{ ok: boolean; op: string; changed: string[]; unchanged: string[]; missing: string[] }>('/api/chat/sessions/bulk', { op, keys, ...args }),
  autoArchiveSessions: (opts: { dry_run?: boolean; active_session?: string } = {}) =>
    post<AutoArchiveSessionsResult>('/api/chat/sessions/auto-archive', opts),
  sessionTemplates: () =>
    get<{ templates: SessionTemplate[] }>('/api/chat/sessions/templates').then((d) => d.templates),
  createSessionTemplate: (body: SessionTemplateInput) =>
    post<{ ok: boolean; template: SessionTemplate }>('/api/chat/sessions/templates', body),
  updateSessionTemplate: (id: string, body: SessionTemplateInput) =>
    put<{ ok: boolean; template: SessionTemplate }>(`/api/chat/sessions/templates/${encodeURIComponent(id)}`, body),
  deleteSessionTemplate: (id: string) =>
    del(`/api/chat/sessions/templates/${encodeURIComponent(id)}`),
  sessionExportUrl: (key: string, format: 'md' | 'json') =>
    `/api/chat/sessions/${encodeURIComponent(key)}/export?format=${format}`,
  shareSession: (key: string) =>
    post<{ ok: boolean; slug: string; name: string; kind: ArtifactKind; readonly: boolean; redacted: boolean }>(
      `/api/chat/sessions/${encodeURIComponent(key)}/share`, {}),
  createChatSession: (opts: { name?: string; agent?: string; model?: string; memory_mode?: MemoryMode; mode?: string; project_id?: string } = {}) =>
    post<ChatSession>('/api/chat/sessions', opts),
  setSessionAgent: (session: string, agent: string) => post(`/api/chat/sessions/${session}/agent`, { agent }),
  setSessionAcpAgent: (session: string, body: { provider: string; provider_agent?: string; model?: string; reasoning_effort?: ReasoningEffort }) =>
    post(`/api/chat/sessions/${session}/acp-agent`, body),
  setSessionModel: (session: string, model: string) => post(`/api/chat/sessions/${session}/model`, { model }),
  setReasoningEffort: (session: string, reasoning_effort: ReasoningEffort) =>
    post(`/api/chat/sessions/${session}/reasoning-effort`, { reasoning_effort }),
  setApprovalMode: (mode: ApprovalMode, session = '') =>
    post<ApprovalModeResult>('/api/chat/mode', { mode, session }),
  setTaskMode: (mode: TaskMode, session = '') => post('/api/chat/task-mode', { mode, session }),

  chatPlanSession: (session: string) =>
    get<ChatPlanWire>(`/api/chat/sessions/${encodeURIComponent(session)}/plan-session`),
  chatPlanActivate: (session: string) =>
    post<{ ok: boolean; session: PlanSession; parked: boolean }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/activate`,
    ),
  chatPlanEdit: (session: string, stepId: string, markdown: string) =>
    post<{ ok: boolean; session: PlanSession }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/edit`, { step_id: stepId, markdown }),
  chatPlanComment: (session: string, stepId: string, text: string) =>
    post<{ ok: boolean; session: PlanSession }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/comment`, { step_id: stepId, text }),
  chatPlanApprove: (session: string, stepId: string) =>
    post<{ ok: boolean; session: PlanSession; complete: boolean; resumed: boolean; task_mode: TaskMode }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/approve`, { step_id: stepId }),
  chatPlanCancel: (session: string) =>
    post<{ ok: boolean; task_mode: TaskMode }>(
      `/api/chat/sessions/${encodeURIComponent(session)}/plan/cancel`),

  optimizePrompt: (prompt: string, context = '') =>
    post<{ optimized?: string; changed?: boolean }>('/api/optimizer/optimize', { prompt, context }),
  transcribeAudio: async (
    blob: Blob,
    opts?: { duplex?: boolean; session?: string },
  ): Promise<{ text?: string; error?: string; filtered?: string; input_origin?: string; disclaimer?: string }> => {
    const fd = new FormData()
    fd.append('audio', blob, 'recording.webm')
    const qs = new URLSearchParams()
    if (opts?.duplex) qs.set('duplex', 'true')
    if (opts?.session) qs.set('session', opts.session)
    const url = qs.toString() ? `/api/stt/transcribe?${qs}` : '/api/stt/transcribe'
    const r = await fetch(url, { method: 'POST', headers: { ...SK }, body: fd })
    const data = await r.json().catch(() => ({}))
    if (!r.ok) return { error: data?.error || `HTTP ${r.status}` }
    return data
  },

  sendChat: (message: string, session: string, meta?: object, queue_mode?: string, input_origin?: string) =>
    post<{ ok: boolean; session?: string; queued?: boolean; steered?: boolean }>('/api/chat?ws=1', { message, session, meta, ...(queue_mode ? { queue_mode } : {}), ...(input_origin ? { input_origin } : {}) }),
  cancelQueued: (session: string, queueId: string) => del(`/api/chat/sessions/${encodeURIComponent(session)}/queue/${encodeURIComponent(queueId)}`),
  stopChat: (session: string, force = false) => post(`/api/chat/sessions/${session}/stop${force ? '?force=true' : ''}`),
  approve: (session: string, action: string, request_id?: string) =>
    post<{ ok: boolean; mode?: ApprovalMode; approval_screening?: ApprovalScreeningVerdict }>(
      `/api/chat/sessions/${session}/approve`, { action, request_id }),

  sideOpen: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/side/open`, {}),
  sideTurn: (session: string, question: string) => post<{ ok: boolean; run_id: string }>(`/api/chat/sessions/${session}/side/turn`, { question }),
  sideClose: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/side/close`, {}),
  undoChat: (session: string, n = 1) =>
    post<{ ok: boolean; turns_undone: number; notice: string }>(`/api/chat/sessions/${session}/undo`, { n }),
  rewindPreview: (session: string, turn: number) =>
    get<RewindPreviewWire>(`/api/chat/sessions/${session}/rewind?turn=${turn}`),
  rewindToTurn: (session: string, turn: number) =>
    post<RewindApplyWire>(`/api/chat/sessions/${session}/rewind`, { turn, confirm: true }),

  autonudgeGet: (session: string) =>
    get<{ enabled: boolean; loop: NudgeLoop | null }>(`/api/autonudge/session/${encodeURIComponent(session)}`),
  autonudgeStart: (body: { session_name: string; message: string; idle_secs?: number; max_cycles?: number }) =>
    post<{ ok: boolean; loop: NudgeLoop }>('/api/autonudge', body),
  autonudgeUpdate: (loopId: string, body: { message?: string; idle_secs?: number; max_cycles?: number; active?: boolean }) =>
    patch<{ ok: boolean; loop: NudgeLoop }>(`/api/autonudge/${encodeURIComponent(loopId)}`, body),
  autonudgeDelete: (loopId: string) => del(`/api/autonudge/${encodeURIComponent(loopId)}`),

  renameSession: (session: string, title: string) =>
    patch<{ ok: boolean; title: string }>(`/api/chat/sessions/${encodeURIComponent(session)}/title`, { title }),
  generateTitle: (session: string) =>
    post<{ ok: boolean; title?: string }>(`/api/chat/sessions/${encodeURIComponent(session)}/generate-title`),

  regenerate: (session: string) => post<{ ok: boolean }>(`/api/chat/sessions/${session}/regenerate`),
  switchVariant: (session: string, index: number) =>
    post<{ ok: boolean; index: number }>(`/api/chat/sessions/${session}/switch-variant`, { index }),
  editResend: (session: string, content: string, ts?: string, index?: number, client_ts?: string, rewind?: boolean) =>
    post<{ ok: boolean; rewound: number }>(`/api/chat/sessions/${session}/edit-resend`,
      { content, ...(ts ? { ts } : {}), ...(index !== undefined ? { index } : {}), ...(client_ts ? { client_ts } : {}), ...(rewind ? { rewind: true } : {}) }),
  interruptChat: (session: string, queueId?: string) =>
    post<{ ok: boolean }>(`/api/chat/sessions/${session}/interrupt`, queueId ? { queue_id: queueId } : {}),
  forkSession: (session: string, at_message_index?: number) =>
    post<{ ok: boolean; key: string; title: string; messages: number; prompt?: string }>(`/api/chat/sessions/${session}/fork`, at_message_index != null ? { at_message_index } : {}),
  forkRewound: (session: string, index: number, snapshot_index?: number) =>
    post<{ ok: boolean; key: string; title: string; messages: number }>(`/api/chat/sessions/${session}/fork-rewound`, { index, ...(snapshot_index != null ? { snapshot_index } : {}) }),
  voiceSynthesize: (text: string, session = '') => post<{ ok: boolean; chunks: number }>('/api/voice/synthesize', { text, session }),

  voiceProfiles: () => get<{ profiles: VoiceProfile[]; bindings: VoiceBindings }>('/api/voice/profiles'),
  voiceProfileCreate: (body: VoiceProfileDraft) => post<VoiceProfile>('/api/voice/profiles', body),
  voiceProfileUpdate: (id: string, body: Partial<VoiceProfileDraft>) =>
    put<VoiceProfile>(`/api/voice/profiles/${encodeURIComponent(id)}`, body),
  voiceProfileDelete: (id: string) => del(`/api/voice/profiles/${encodeURIComponent(id)}`),
  voiceProfileConsentRecord: (id: string, consent_text: string) =>
    post<VoiceProfile>(`/api/voice/profiles/${encodeURIComponent(id)}/consent`, { consent_text }),
  voiceProfileConsentVerify: (id: string) =>
    post<VoiceProfile>(`/api/voice/profiles/${encodeURIComponent(id)}/consent/verify`, {}),
  voiceProfileConsentRevoke: (id: string) => del(`/api/voice/profiles/${encodeURIComponent(id)}/consent`),
  voiceProfileLock: (id: string, history_index: number) =>
    post<VoiceProfile>(`/api/voice/profiles/${encodeURIComponent(id)}/lock`, { history_index }),
  voiceProfileUnlock: (id: string) => post<VoiceProfile>(`/api/voice/profiles/${encodeURIComponent(id)}/unlock`, {}),
  voiceBindingSet: (surface: string, profile_id: string) =>
    put<{ bindings: VoiceBindings; warning: string }>('/api/voice/bindings', { surface, profile_id }),
  voiceBindingClear: (surface: string) => del(`/api/voice/bindings?surface=${encodeURIComponent(surface)}`),
  voiceMigrate: (name = '') => post<VoiceProfile>('/api/voice/migrate', name ? { name } : {}),
  voiceResolve: (surface: string) =>
    get<VoiceResolution>(`/api/voice/resolve?surface=${encodeURIComponent(surface)}`),

  uLoops: (params?: { projectId?: string; kind?: LoopKind }) => {
    const q = new URLSearchParams()
    if (params?.projectId) q.set('project_id', params.projectId)
    if (params?.kind) q.set('kind', params.kind)
    const qs = q.toString()
    return get<{ loops: Loop[] }>(`/api/loops${qs ? `?${qs}` : ''}`).then((d) => d.loops)
  },
  uLoop: (id: string) => get<Loop>(`/api/loops/${encodeURIComponent(id)}`),
  uLoopReport: (id: string) => get<{ report: string; log: string }>(`/api/loops/${encodeURIComponent(id)}/report`),
  uLoopStreamUrl: (id: string) => `/api/loops/${encodeURIComponent(id)}/stream`,
  classifyULoop: (kind: LoopKind, task: string) =>
    post<UnifiedLoopClassification>('/api/loops/classify', { kind, task }),
  grillTree: (id: string) => post<GrillTreeResult>(`/api/loops/${encodeURIComponent(id)}/grill-tree`, {}),
  validateULoop: (body: Record<string, unknown>) => post<LoopValidation>('/api/loops/validate', body),
  createULoop: (body: Record<string, unknown>) => post<Loop | CreatedLoopRun>('/api/loops', body),
  updateULoop: (id: string, body: Record<string, unknown>) => put<Loop>(`/api/loops/${encodeURIComponent(id)}`, body),
  uLoopAction: (id: string, action: 'start' | 'pause' | 'resume' | 'stop') =>
    fetch(`/api/loops/${encodeURIComponent(id)}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ action }) }).then(j<Loop>),
  uLoopNudge: (id: string, text: string, taskId?: string) => post(`/api/loops/${encodeURIComponent(id)}/nudge`, taskId ? { text, task_id: taskId } : { text }),
  deleteULoop: (id: string) => fetch(`/api/loops/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw await apiError(r) }),
  uLoopQueue: (id: string, taskIds: string[], action: 'queue' | 'unqueue' = 'queue') =>
    post<{ ok: boolean; queued_task_ids: string[] }>(`/api/loops/${encodeURIComponent(id)}/queue`, { task_ids: taskIds, action }),
  uLoopAutopilot: (id: string, on: boolean) =>
    post<{ ok: boolean; autopilot: boolean }>(`/api/loops/${encodeURIComponent(id)}/autopilot`, { on }),
  uLoopPlanState: (id: string) => get<LoopPlanState>(`/api/loops/${encodeURIComponent(id)}/plan-session`),
  uLoopPlanSession: (id: string) => get<LoopPlanState>(`/api/loops/${encodeURIComponent(id)}/plan-session`).then((d) => d.session),
  uLoopPlanStart: (id: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/start`, {}),
  uLoopPlanRetry: (id: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/retry`, {}),
  uLoopPlanApprove: (id: string, stepId: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/approve`, { step_id: stepId }),
  uLoopPlanComment: (id: string, stepId: string, text: string) => post<{ ok: boolean; planning: boolean }>(`/api/loops/${encodeURIComponent(id)}/plan/comment`, { step_id: stepId, text }),
  uLoopPlanEdit: (id: string, stepId: string, markdown: string) => post<{ ok: boolean; session: PlanSession }>(`/api/loops/${encodeURIComponent(id)}/plan/edit`, { step_id: stepId, markdown }),

  designDefaultTokens: (scheme: 'light' | 'dark' = 'light') =>
    get<{ tokens: Record<string, unknown>; schema: Record<string, unknown>; resolved: Record<string, unknown>; css: string; overrides: Record<string, unknown>; scheme: string }>(`/api/design/tokens/default?scheme=${scheme}`),
  uLoopDesignTokens: (id: string, scheme: 'light' | 'dark' = 'light') =>
    get<{ resolved: Record<string, unknown>; css: string; overrides: Record<string, unknown>; scheme: string }>(`/api/loops/${encodeURIComponent(id)}/design/tokens?scheme=${scheme}`),

  notifications: () => get<{ notifications: NotificationItem[]; unread: number }>('/api/notifications'),
  ackNotification: (ts: string) => post('/api/notifications/ack', { ts }),

  status: () => get<DashboardStatus>('/api/status'),
  approvals: () => get<PendingApproval[]>('/api/approvals'),
  resolveApproval: (id: string, action: 'approve' | 'reject') =>
    post<{ ok: boolean }>(`/api/approvals/${encodeURIComponent(id)}/${action}`, {}),
  pushStatus: () => get<PushStatus>('/api/push'),
  pushSubscribe: (device_id: string, subscription: unknown) =>
    post<{ ok: boolean; device_id: string }>('/api/push/subscribe', { device_id, subscription }),
  pushUnsubscribe: (device_id: string) =>
    post<{ ok: boolean }>('/api/push/unsubscribe', { device_id }),
  pushRelayRegister: (device_id: string, platform: string, token: string) =>
    post<{ ok: boolean; device_id: string }>('/api/push/relay-register', {
      device_id,
      platform,
      token,
    }),
  pushRelayUnregister: (device_id: string) =>
    post<{ ok: boolean }>('/api/push/relay-unregister', { device_id }),
  inboxOpen: () => get<InboxItem[]>('/api/inbox/open'),
  triggersHistory: (limit = 20, offset = 0) =>
    get<{
      runs: ScheduleRun[]; total: number; schedule_total?: number; kinds?: string[]
      summaries?: number; did_ids?: string[]; suppressed_ids?: string[]; suppressed?: number
    }>(`/api/triggers/history?limit=${limit}&offset=${offset}`),
  unackNotification: (ts: string) => post('/api/notifications/unack', { ts }),
  ackAllNotifications: () => post('/api/notifications/ack-all'),
  deleteNotification: (ts: string) => fetch('/api/notifications', { method: 'DELETE', headers: { 'Content-Type': 'application/json', ...SK }, body: JSON.stringify({ ts }) }).then(async (r) => { if (!r.ok) throw await apiError(r) }),
  clearNotifications: () => post('/api/notifications/clear'),

  triggers: (type?: 'schedule' | 'lifecycle' | 'event') =>
    get<{ triggers: Trigger[]; server_tz: string; owner?: string }>(
      `/api/triggers${type ? `?type=${type}` : ''}`,
    ),
  triggersWeek: (start?: Date, days = 7, until?: Date) => {
    const qs = new URLSearchParams()
    if (start) qs.set('start', start.toISOString())
    qs.set('days', String(days))
    if (until) qs.set('until', until.toISOString())
    return get<WeekProjection>(`/api/triggers/week?${qs.toString()}`)
  },
  eventTriggers: () => get<{ triggers: Trigger[] }>('/api/triggers?type=event').then((d) => d.triggers),
  createEvent: (body: {
    name?: string; pattern: EventPattern
    sender_glob?: string; address_glob?: string; key_glob?: string; content_re?: string
    event_glob?: string
    max_fires?: number; action: { provider: string; config: Record<string, unknown> }
  }) => post<Trigger & { warning?: string }>('/api/triggers', { trigger_type: 'event', ...body }),
  updateEventTrigger: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/event:${encodeURIComponent(id)}`, body),
  deleteEventTrigger: (id: string) => del(`/api/triggers/event:${encodeURIComponent(id)}`),
  toggleEventTrigger: (id: string, enabled?: boolean) =>
    post<{ ok: boolean; trigger: Trigger }>(`/api/triggers/event:${encodeURIComponent(id)}/toggle`, enabled === undefined ? {} : { enabled }),
  runEventTrigger: (id: string, body?: { key?: string; value?: string; event_type?: string }) =>
    post<EventFireResult>(`/api/triggers/event:${encodeURIComponent(id)}/run`, body ?? {}),
  testEventTrigger: (id: string, body?: { key?: string; value?: string; event_type?: string }) =>
    post<EventFireResult>(`/api/triggers/event:${encodeURIComponent(id)}/test`, { ...(body ?? {}), test: true }),
  eventTriggerHistory: (id: string) =>
    get<{ runs: never[]; total: number; supported: boolean; reason: string; fire_count: number; last_fired_at: number }>(`/api/triggers/event:${encodeURIComponent(id)}/history`),
  schedules: () => get<{ triggers: Trigger[]; server_tz: string }>('/api/triggers?type=schedule')
    .then((d) => ({ jobs: d.triggers.map((t) => ({ ...t, id: t.raw_id })) as unknown as ScheduleJob[], server_tz: d.server_tz })),
  createSchedule: (body: Record<string, unknown>) =>
    post<{ ok: boolean; trigger: Trigger }>('/api/triggers', { trigger_type: 'schedule', ..._scheduleBodyToWire(body) }),
  updateSchedule: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/schedule:${encodeURIComponent(id)}`, _scheduleBodyToWire(body)),
  deleteSchedule: (id: string) => del(`/api/triggers/schedule:${encodeURIComponent(id)}`),
  runSchedule: (id: string, dryRun = false) =>
    post<TriggerRunResult>(`/api/triggers/schedule:${encodeURIComponent(id)}/run`, dryRun ? { dry_run: true } : undefined),
  enableSchedule: (id: string, enabled: boolean) => post(`/api/triggers/schedule:${encodeURIComponent(id)}/toggle`, { enabled }),
  scheduleToChat: (id: string) => post<{ ok: boolean; session: string }>(`/api/triggers/schedule:${encodeURIComponent(id)}/to-chat`),
  triggerHistory: (triggerId: string, limit = 10, offset = 0) =>
    get<{ runs: ScheduleRun[]; total: number; supported?: boolean; reason?: string }>(
      `/api/triggers/${encodeURIComponent(triggerId)}/history?limit=${limit}&offset=${offset}`),
  triggerRunDetail: (triggerId: string, runId: string) =>
    get<{ run: ScheduleRun }>(
      `/api/triggers/${encodeURIComponent(triggerId)}/history/${encodeURIComponent(runId)}`).then((d) => d.run),
  scheduleHistory: (id: string, limit = 10, offset = 0) => get<{ runs: ScheduleRun[]; total: number }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history?limit=${limit}&offset=${offset}`),
  scheduleRunDetail: (id: string, runId: string) => get<{ run: ScheduleRun }>(`/api/triggers/schedule:${encodeURIComponent(id)}/history/${encodeURIComponent(runId)}`).then((d) => d.run),
  triggerVariables: () => get<TriggerVariables>('/api/triggers/variables'),

  tasks: (opts: { project?: string; task_list?: string; status?: string; limit?: number; offset?: number; mine?: boolean } = {}) => {
    const qs = new URLSearchParams()
    if (opts.project) qs.set('project', opts.project)
    if (opts.task_list) qs.set('task_list', opts.task_list)
    if (opts.status) qs.set('status', opts.status)
    if (opts.limit) qs.set('limit', String(opts.limit))
    if (opts.offset) qs.set('offset', String(opts.offset))
    if (opts.mine) qs.set('mine', '1')
    const s = qs.toString()
    return get<{ tasks: TaskItem[]; total: number; owner?: string }>(`/api/tasks${s ? `?${s}` : ''}`)
  },
  allTasks: async (opts: { project?: string; task_list?: string; status?: string; mine?: boolean } = {}) => {
    const tasks: TaskItem[] = []
    let owner: string | undefined
    let total = 0
    do {
      const page = await api.tasks({ ...opts, limit: 500, offset: tasks.length })
      tasks.push(...page.tasks)
      owner ??= page.owner
      total = page.total
      if (!page.tasks.length) break
    } while (tasks.length < total)
    return { tasks, total, owner }
  },
  task: (id: string, provider?: string) => get<TaskItem>(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskGraph: (provider?: string) => get<TaskGraphData>(`/api/tasks/graph${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  createTask: (body: Record<string, unknown>) => post<TaskItem>('/api/tasks', body),
  updateTask: (id: string, body: Record<string, unknown>) => put<TaskItem>(`/api/tasks/${encodeURIComponent(id)}`, body),
  deleteTask: (id: string, provider?: string) => del(`/api/tasks/${encodeURIComponent(id)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  taskComments: (id: string, provider?: string) => get<{ comments: TaskComment[] }>(`/api/tasks/${encodeURIComponent(id)}/comments${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`).then((d) => d.comments),
  addTaskComment: (id: string, body: string, provider?: string) => post<TaskComment>(`/api/tasks/${encodeURIComponent(id)}/comments`, { body, provider }),
  deleteTaskComment: (id: string, commentId: string, provider?: string) => del(`/api/tasks/${encodeURIComponent(id)}/comments/${encodeURIComponent(commentId)}${provider ? `?provider=${encodeURIComponent(provider)}` : ''}`),
  readyTasks: (opts: { project?: string; task_list_id?: string } = {}) => {
    const qs = new URLSearchParams()
    if (opts.project) qs.set('project', opts.project)
    if (opts.task_list_id) qs.set('task_list_id', opts.task_list_id)
    const s = qs.toString()
    return get<{ tasks: TaskItem[] }>(`/api/tasks/ready${s ? `?${s}` : ''}`).then((d) => d.tasks)
  },
  searchTasks: (body: Record<string, unknown>) => post<{ tasks: TaskItem[]; total: number }>('/api/tasks/search', body),
  allSearchTasks: async (body: Record<string, unknown>) => {
    const tasks: TaskItem[] = []
    let total = 0
    do {
      const page = await api.searchTasks({ ...body, limit: 500, offset: tasks.length })
      tasks.push(...page.tasks)
      total = page.total
      if (!page.tasks.length) break
    } while (tasks.length < total)
    return { tasks, total }
  },

  projects: () => get<{ projects: ProjectItem[] }>('/api/projects').then((d) => d.projects),
  project: (id: string) => get<ProjectItem>(`/api/projects/${encodeURIComponent(id)}`),
  projectLinked: (id: string) => get<{ loops: ProjectLinkedItem[]; code: ProjectLinkedItem[]; artifacts: { slug: string; name: string; kind: string }[]; chats: { key: string; title: string; running: boolean }[]; knowledge: ProjectKnowledgeItem[] }>(`/api/projects/${encodeURIComponent(id)}/linked`),
  projectWork: (id: string) => get<WorkBoard>(`/api/projects/${encodeURIComponent(id)}/work`),
  claimWork: (id: string, target_id: string, holder: string) =>
    post<{ granted: boolean; claim: WorkClaim | null; reason: string }>(`/api/projects/${encodeURIComponent(id)}/work/claim`, { target_id, holder }),
  releaseWork: (id: string, target_id: string, holder: string) =>
    post<{ released: boolean; claim: WorkClaim | null; reason: string }>(`/api/projects/${encodeURIComponent(id)}/work/release`, { target_id, holder }),
  createProject: (body: { name: string; brief?: string; agent_instructions_template?: string; workspace_dir?: string; name_locked?: boolean }) => post<ProjectItem>('/api/projects', body),
  updateProject: (id: string, body: Record<string, unknown>) => put<ProjectItem>(`/api/projects/${encodeURIComponent(id)}`, body),
  deleteProject: (id: string, force = false) => del(`/api/projects/${encodeURIComponent(id)}${force ? '?force=true' : ''}`),
  regenerateContextAdapters: (id: string) =>
    post<{ ok: boolean; written: string[]; errors: { file: string; error: string }[]; workspace_dir: string }>(
      `/api/projects/${encodeURIComponent(id)}/context-adapters/regenerate`, {}),
  taskLists: (projectId?: string) => get<{ task_lists: TaskListItem[] }>(`/api/task-lists${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`).then((d) => d.task_lists),
  createTaskList: (body: Record<string, unknown>) => post<TaskListItem>('/api/task-lists', body).then((list) => {
    emitTaskListCreated(list.project_id)
    return list
  }),
  updateTaskList: (id: string, body: Record<string, unknown>) => put<TaskListItem>(`/api/task-lists/${encodeURIComponent(id)}`, body),
  deleteTaskList: (id: string) => del(`/api/task-lists/${encodeURIComponent(id)}`),
  resetTaskList: (id: string) => post<{ ok: boolean; reset_task_ids: string[] }>(`/api/task-lists/${encodeURIComponent(id)}/reset`, { confirm: true }),


  prompts: (kind?: PromptKind) => get<PromptItem[]>(`/api/prompts${kind ? `?kind=${kind}` : ''}`),
  prompt: (name: string) => get<PromptItem>(`/api/prompts/${encodeURIComponent(name)}`),
  createPrompt: (body: Record<string, unknown>) => post<{ ok: boolean; name: string; prompt: PromptItem }>('/api/prompts', body),
  savePrompt: (name: string, body: Record<string, unknown>) => put<{ ok: boolean; prompt: PromptItem }>(`/api/prompts/${encodeURIComponent(name)}`, body),
  deletePrompt: (name: string) => del(`/api/prompts/${encodeURIComponent(name)}`),
  renderPrompt: (name: string, variables: Record<string, unknown>) => post<{ name: string; rendered: string }>(`/api/prompts/${encodeURIComponent(name)}/render`, { variables }),
  launchCampaignTemplate: (name: string, variables: Record<string, unknown>, projectId?: string) =>
    post<{ ok: boolean; loop_id: string; kind: LoopKind; started: boolean }>(
      `/api/prompts/${encodeURIComponent(name)}/launch`, projectId ? { variables, project_id: projectId } : { variables }),
  previewPrompt: (body: { content: string; variables?: PromptVariable[]; values?: Record<string, unknown> }) => post<PromptPreview>('/api/prompts/preview', body),
  promptSyntax: () => get<PromptSyntax>('/api/prompts/syntax'),
  snippets: () => get<PromptSnippet[]>('/api/prompt-snippets'),
  snippet: (name: string) => get<PromptSnippet>(`/api/prompt-snippets/${encodeURIComponent(name)}`),
  createSnippet: (body: Record<string, unknown>) => post<{ ok: boolean; name: string; snippet: PromptSnippet }>('/api/prompt-snippets', body),
  saveSnippet: (name: string, body: Record<string, unknown>) => put<{ ok: boolean; snippet: PromptSnippet }>(`/api/prompt-snippets/${encodeURIComponent(name)}`, body),
  deleteSnippet: (name: string) => fetch(`/api/prompt-snippets/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { ...SK } }).then(async (r) => { if (!r.ok) throw await apiError(r) }),
  renderSnippet: (name: string, variables: Record<string, unknown>) => post<{ name: string; rendered: string }>(`/api/prompt-snippets/${encodeURIComponent(name)}/render`, { variables }),
  promptBindings: () => get<PromptBindings>('/api/prompts/bindings'),
  setPromptBinding: (use_case: string, ref: string) => put<PromptBindings>('/api/prompts/bindings', { use_case, ref }),

  skills: () => get<SkillItem[]>('/api/skills'),
  skillFiles: (name: string, path?: string) => get<{ name: string; files?: SkillFile[]; path?: string; content?: string }>(`/api/skills/${encodeURIComponent(name)}/files${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  skillContent: (name: string) => get<{ content?: string }>(`/api/skills/${encodeURIComponent(name)}`).then((d) => d.content ?? ''),
  createSkill: (name: string, content: string) => post<{ ok: boolean }>('/api/skills', { name, content }),
  updateSkill: (name: string, content: string) => put<{ ok: boolean }>(`/api/skills/${encodeURIComponent(name)}`, { content }),
  deleteSkill: (name: string) => del(`/api/skills/${encodeURIComponent(name)}`),
  verifySkill: (name: string) => post<SkillIntegrity>(`/api/skills/${encodeURIComponent(name)}/verify`),
  learningProposals: (opts?: { kind?: string; tier?: string; flagged?: boolean }) => {
    const q = new URLSearchParams()
    if (opts?.kind) q.set('kind', opts.kind)
    if (opts?.tier) q.set('tier', opts.tier)
    if (opts?.flagged) q.set('flagged', '1')
    const qs = q.toString()
    return get<LearningInbox>(`/api/learning/proposals${qs ? `?${qs}` : ''}`)
  },
  learningProposal: (id: string) =>
    get<Record<string, unknown>>(`/api/learning/proposals/${encodeURIComponent(id)}`),
  acceptLearningProposal: (id: string) =>
    post<{ ok: boolean }>(`/api/learning/proposals/${encodeURIComponent(id)}/accept`, {}),
  rejectLearningProposal: (id: string) =>
    del(`/api/learning/proposals/${encodeURIComponent(id)}`),
  learningStagingWeek: (days = 7) =>
    get<StagingWeek>(`/api/learning/staging/week?days=${days}`),
  learningHealth: (days = 7) =>
    get<LearningHealth>(`/api/learning/health?days=${days}`),
  identityReport: (days?: number) =>
    get<IdentityReportView>(`/api/learning/identity-report${days === undefined ? '' : `?days=${days}`}`),
  deliverIdentityReport: (days?: number) =>
    post<IdentityReportDelivery>(
      `/api/learning/identity-report${days === undefined ? '' : `?days=${days}`}`,
      {},
    ),
  judgeBench: () => get<JudgeBenchView>('/api/evals/judge-bench'),
  ablation: () => get<AblationView>('/api/evals/ablation'),
  learningBenchmark: () => get<BenchmarkView>('/api/evals/learning-benchmark'),
  retrievalBench: () => get<RetrievalBenchView>('/api/evals/retrieval'),
  retrievalLabelCard: (store: string) =>
    get<RetrievalLabelCard>(`/api/evals/retrieval/card?store=${encodeURIComponent(store)}`),
  saveRetrievalLabels: (store: string, labels: Record<string, string[]>) =>
    post<{ ok: boolean; store: string; queries: number; hand_labelled: number }>(
      '/api/evals/retrieval/labels', { store, labels }),
  evalStudies: () => get<{ studies: StudyRow[] }>('/api/evals/studies'),
  evalStudy: (studyId: string) =>
    get<StudyView>(`/api/evals/studies/${encodeURIComponent(studyId)}`),
  evalFieldMetrics: () => get<{ subjects: FieldMetricsRow[] }>('/api/evals/field-metrics'),
  pendingSkillProposalCount: () =>
    get<SkillProposalFeed>('/api/skills/proposals').then((feed) => feed.proposals.length),
  skillProposals: () => get<SkillProposalFeed>('/api/skills/proposals'),
  skillProposalDetail: (id: string) => get<SkillProposalDetail>(`/api/skills/proposals/${encodeURIComponent(id)}`),
  acceptSkillProposal: (id: string, edits?: { description?: string; procedure_md?: string }) =>
    post<{ ok: boolean; name: string; version: number }>(`/api/skills/proposals/${encodeURIComponent(id)}/accept`, edits ?? {}),
  rejectSkillProposal: (id: string) => del(`/api/skills/proposals/${encodeURIComponent(id)}`),
  learningSummary: (days?: number) => get<LearningSummary>(`/api/learning/summary${days ? `?days=${days}` : ''}`),
  ephemeralSkills: (session: string) =>
    get<{ drafts: EphemeralDraft[] }>(`/api/skills/ephemeral/${encodeURIComponent(session)}`).then((d) => d.drafts),
  promoteEphemeralSkill: (session: string, payload: { slug: string; scope: 'agent' | 'global'; agent?: string; title?: string; body?: string }) =>
    post<{ ok: boolean; name: string; scope: string }>(`/api/skills/ephemeral/${encodeURIComponent(session)}/promote`, payload),
  discardEphemeralSkill: (session: string, slug: string) =>
    del(`/api/skills/ephemeral/${encodeURIComponent(session)}/${encodeURIComponent(slug)}`),
  skillMarketplaces: () => get<SkillMarketplace[]>('/api/skills/marketplaces'),
  searchSkillsCounted: (q: string, marketplace?: string, limit = 30) =>
    get<{ results: SkillSearchResult[]; counts?: Record<string, number>; installable_sources?: number }>(`/api/skills/search?q=${encodeURIComponent(q)}&limit=${limit}${marketplace ? `&marketplace=${encodeURIComponent(marketplace)}` : ''}`).then((d) => ({ results: d.results, counts: d.counts ?? {}, installableSources: d.installable_sources ?? 0 })),
  searchSkills: (q: string, marketplace?: string, limit = 30) =>
    get<{ results: SkillSearchResult[] }>(`/api/skills/search?q=${encodeURIComponent(q)}&limit=${limit}${marketplace ? `&marketplace=${encodeURIComponent(marketplace)}` : ''}`).then((d) => d.results),
  skillMarketplaceDetail: (id: string, marketplace = 'skills.sh') =>
    get<SkillMarketplaceDetail>(`/api/skills/marketplace/detail?id=${encodeURIComponent(id)}&marketplace=${encodeURIComponent(marketplace)}`),
  installSkill: async (id: string, marketplace = 'skills.sh', force = false): Promise<SkillInstallResult> => {
    try {
      const r = await fetch('/api/skills/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...SK },
        body: JSON.stringify({ id, marketplace, force }),
      })
      const data = await r.json().catch(() => null)
      if (data && typeof data === 'object') return { httpStatus: r.status, ...data } as SkillInstallResult
      return { ok: false, error: `HTTP ${r.status}`, httpStatus: r.status }
    } catch (e) {
      return { ok: false, error: String((e as Error)?.message || e), httpStatus: 0 }
    }
  },

  tools: () => get<{ tools: ToolItem[] }>('/api/tools').then((d) => d.tools),
  manifest: () => get<Manifest>('/api/manifest'),
  toolsIndex: () => get<{
    tools: ToolItem[]; load_failures?: ToolLoadFailure[]
  }>('/api/tools'),
  invokeTool: (tool: string, args: Record<string, unknown>, provider?: string, confirmRisk?: 'destructive') =>
    post<ToolInvokeResult>('/api/tools/invoke', { tool, arguments: args, provider, confirm_risk: confirmRisk }),
  mcpServers: () => get<McpServer[]>('/api/mcp'),
  toggleMcpServer: (name: string, enabled: boolean) => post('/api/mcp/toggle', { name, enabled }),
  toggleMcpTool: (server: string, tool: string, enabled: boolean) => post('/api/mcp/toggle-tool', { server, tool, enabled }),
  toggleTool: (provider: string, name: string, enabled: boolean) => post('/api/tools/toggle', { provider, name, enabled }),
  toggleToolProvider: (provider: string, enabled: boolean) => post('/api/tools/provider-toggle', { provider, enabled }),
  mcpPoolStats: () => get<McpPoolStats>('/api/mcp/pool-stats'),
  probeMcp: () => post<{ ok?: boolean }>('/api/mcp/probe'),
  reconnectMcp: (name: string) => post<McpServer>(`/api/mcp/probe/${encodeURIComponent(name)}`),
  toggleAllMcp: (enabled: boolean) => post('/api/mcp/toggle-all', { enabled }),
  addMcpServer: (name: string, body: { command: string; args?: string[]; env?: Record<string, string> }) =>
    put<{ ok?: boolean; name: string }>(`/api/mcp/servers/${encodeURIComponent(name)}`, body),
  removeMcpServer: (name: string) => del(`/api/mcp/servers/${encodeURIComponent(name)}`),
  importableMcp: () => get<{ servers: ImportableMcpServer[] }>('/api/mcp/importable').then((r) => r.servers),
  importMcpServer: (name: string) =>
    post('/api/mcp/apply', { changes: [{ name, gideon: true, globalMcp: false, ccGlobal: true }] }),

  system: () => get<SystemInfo>('/api/system'),
  authStatus: () => get<AuthStatus>('/api/auth-status'),

  useCaseSettings: (useCase: string) =>
    get<{ use_case: string; settings: Record<string, unknown> }>(`/api/models/use-cases/${encodeURIComponent(useCase)}/settings`).then((d) => d.settings),
  saveUseCaseSettings: (useCase: string, settings: Record<string, unknown>) =>
    put<{ ok: boolean; settings: Record<string, unknown> }>(`/api/models/use-cases/${encodeURIComponent(useCase)}/settings`, settings),

  createTerminal: (cwd?: string, sandbox?: string) => post<{ session_id: string; shell: string; cwd: string; sandbox: string }>('/api/terminal/sessions', { ...(cwd ? { cwd } : {}), ...(sandbox ? { sandbox } : {}) }),
  sandboxProviders: () => get<{ providers: Array<{ name: string; display_name: string; available: boolean }> }>('/api/sandbox/providers'),
  terminalSessions: () => get<{ enabled?: boolean; persist_available?: boolean; sessions: Array<{ session_id: string; pid?: number; alive?: boolean; cols?: number; rows?: number; connected?: boolean; cwd: string; shell: string; label?: string }> }>('/api/terminal/sessions'),
  deleteTerminal: (id: string) => del(`/api/terminal/sessions/${encodeURIComponent(id)}`),

  hooks: () => get<{ triggers: Trigger[] }>('/api/triggers?type=lifecycle').then((d) => d.triggers.map(_triggerToHook)),
  actionProviders: () => get<{ providers: ActionProvider[] }>('/api/action-providers').then((d) => d.providers),
  createHook: (body: Record<string, unknown>) =>
    post<{ ok: boolean; trigger: Trigger }>('/api/triggers', {
      trigger_type: 'lifecycle', name: body.name, event: body.event, matcher: body.matcher,
      action: { provider: body.provider, config: body.provider_config ?? {} },
    }).then((r) => ({ ok: r.ok, hook: _triggerToHook(r.trigger) })),
  updateHook: (id: string, body: Record<string, unknown>) =>
    put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/lifecycle:${encodeURIComponent(id)}`,
      'provider' in body || 'provider_config' in body
        ? { ...body, action: { provider: body.provider, config: body.provider_config ?? {} } }
        : body,
    ).then((r) => ({ ok: r.ok, hook: _triggerToHook(r.trigger) })),
  deleteHook: (id: string) => del(`/api/triggers/lifecycle:${encodeURIComponent(id)}`),
  toggleHook: (id: string) => post(`/api/triggers/lifecycle:${encodeURIComponent(id)}/toggle`, {}),
  testHook: (id: string, context?: string) => post<{ ok: boolean; result: { stdout: string; stderr: string; exit_code: number; error: string; duration_ms: number } }>(`/api/triggers/lifecycle:${encodeURIComponent(id)}/test`, { context: context ?? 'test' }),

  storeTriggers: () => get<{ triggers: Trigger[] }>('/api/triggers?type=store').then((d) => d.triggers),
  toggleStoreTrigger: (rawId: string, enabled: boolean) =>
    post(`/api/triggers/store:${encodeURIComponent(rawId)}/toggle`, { enabled }),
  updateStoreTrigger: (rawId: string, body: {
    delivery?: string; failure_delivery?: string; failure_policy?: Record<string, unknown>
  }) => put<{ ok: boolean; trigger: Trigger }>(`/api/triggers/store:${encodeURIComponent(rawId)}`, body),
  deleteStoreTrigger: (rawId: string) => del(`/api/triggers/store:${encodeURIComponent(rawId)}`),
  runStoreTrigger: (rawId: string, dryRun = false) =>
    post<TriggerRunResult>(`/api/triggers/store:${encodeURIComponent(rawId)}/run`, dryRun ? { dry_run: true } : {}),
  viewRender: (surface: string) =>
    post<{ refreshed: string[]; served_cache: { trigger_id: string; reason: string }[] }>(
      '/api/triggers/view/render', { surface }),

  knowledgeStats: () => get<KnowledgeStats>('/api/knowledge/stats'),
  knowledgeItems: (params?: { q?: string; type?: string; page?: number; limit?: number; includeArchived?: boolean }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.type) qs.set('type', params.type)
    if (params?.includeArchived) qs.set('include_archived', '1')
    qs.set('page', String(params?.page ?? 1)); qs.set('limit', String(params?.limit ?? 50))
    return get<{ items: KnowledgeItem[]; total: number; page: number; limit: number }>(`/api/knowledge/items?${qs}`)
  },
  lexiconTerms: (opts: { source?: string; search?: string } = {}) =>
    get<{ terms: LexiconTerm[]; total: number }>(
      `/api/lexicon/terms?source=${encodeURIComponent(opts.source || '')}&search=${encodeURIComponent(opts.search || '')}`),
  lexiconAddTerm: (canonical: string, aliases?: string[]) =>
    post<{ ok: boolean; id: string }>('/api/lexicon/terms', { canonical, aliases: aliases || [] }),
  lexiconSetTermEnabled: (id: string, enabled: boolean) =>
    patch<{ ok: boolean }>(`/api/lexicon/terms/${encodeURIComponent(id)}`, { enabled }),
  lexiconDeleteTerm: (id: string) => del(`/api/lexicon/terms/${encodeURIComponent(id)}`),
  lexiconRebuild: () => post<{ ok: boolean; synced: number; total: number }>('/api/lexicon/rebuild'),
  lexiconCorrections: () => get<{ corrections: LexiconCorrection[] }>('/api/lexicon/corrections'),
  lexiconAddCorrection: (heard: string, meant: string, always = false) =>
    post<{ ok: boolean }>('/api/lexicon/corrections', { heard, meant, always }),
  lexiconSetCorrectionAuto: (id: string, auto_apply: boolean) =>
    patch<{ ok: boolean }>(`/api/lexicon/corrections/${encodeURIComponent(id)}`, { auto_apply }),
  lexiconReset: () => post<{ ok: boolean }>('/api/lexicon/reset'),

  knowledgeItem: (id: string) => get<KnowledgeItem>(`/api/knowledge/items/${encodeURIComponent(id)}`),
  knowledgeRsvp: (id: string) =>
    get<KnowledgeRsvpState>(`/api/capabilities/knowledge/rsvp/${encodeURIComponent(id)}`),
  saveKnowledgeRsvp: (id: string, body: Pick<KnowledgeRsvpState, 'word_index' | 'wpm' | 'chunk_size' | 'content_revision'>) =>
    put<KnowledgeRsvpState>(`/api/capabilities/knowledge/rsvp/${encodeURIComponent(id)}`, body),
  bookmarkKnowledgeRsvp: (id: string, word_index: number, content_revision: string) =>
    post<KnowledgeRsvpState>(`/api/capabilities/knowledge/rsvp/${encodeURIComponent(id)}/bookmark`, { word_index, content_revision }),
  restoreKnowledgeRsvp: (id: string) =>
    post<KnowledgeRsvpState>(`/api/capabilities/knowledge/rsvp/${encodeURIComponent(id)}/restore`, {}),
  knowledgeReadingItem: (id: string) => {
    const encoded = encodeURIComponent(id)
    return Promise.all([
      get<KnowledgeItem>(`/api/knowledge/items/${encoded}`),
      get<{ annotations: KnowledgeAnnotation[] }>(`/api/knowledge/items/${encoded}/annotations`)
        .catch(() => ({ annotations: [] })),
    ]).then(([item, result]): KnowledgeReadingItem => ({ item, annotations: result.annotations }))
  },
  knowledgeGraph: () => get<{
    nodes: { id: string; name?: string; type?: string; x?: number; y?: number; placed?: boolean; degree?: number; cluster?: number | null }[]
    edges: { source: string; target: string; type?: string; weight?: number }[]
  }>('/api/knowledge/graph'),
  knowledgeItemRelated: (id: string) => get<KnowledgeItem[]>(`/api/knowledge/items/${encodeURIComponent(id)}/related`),
  researchReports: () => get<{ reports: ResearchReport[] }>('/api/knowledge/reports'),
  createResearchReport: (body: Partial<ResearchReportInput>) =>
    post<ResearchReport>('/api/knowledge/reports', body),
  updateResearchReport: (id: string, body: Partial<ResearchReportInput>) =>
    put<ResearchReport>(`/api/knowledge/reports/${encodeURIComponent(id)}`, body),
  deleteResearchReport: (id: string) =>
    del(`/api/knowledge/reports/${encodeURIComponent(id)}`),
  runResearchReport: (id: string) =>
    post<{ ok: boolean; report_id: string; outcome?: string; note?: string }>(
      `/api/knowledge/reports/${encodeURIComponent(id)}/run`,
    ),
  knowledgeStaleness: (id: string) =>
    get<KnowledgeStaleness>(`/api/knowledge/items/${encodeURIComponent(id)}/staleness`),
  knowledgeRegenerate: (id: string) =>
    post<{
      ok: boolean
      item_id: string
      already_pending: boolean | null
      proposal: { proposal_id?: string; applied?: boolean; pending?: boolean; reason?: string } | null
    }>(`/api/knowledge/items/${encodeURIComponent(id)}/regenerate`),
  regenerateKnowledgeIntelligence: (scope: 'missing' | 'all' = 'missing') =>
    post<{ queued: number; scope: string }>('/api/knowledge/regenerate-intelligence', { scope }),
  knowledgeEntityItems: (name: string) => get<KnowledgeItem[]>(`/api/knowledge/entities/by-name/${encodeURIComponent(name)}/items`),
  knowledgeEntityRelated: (name: string) =>
    get<{ related: { name: string; entity_type?: string; relation_type: string; outgoing: boolean }[] }>(`/api/knowledge/entities/by-name/${encodeURIComponent(name)}/related`),
  generateKnowledgeIntelligence: (id: string) => post<KnowledgeItem>(`/api/knowledge/items/${encodeURIComponent(id)}/generate-intelligence`),
  knowledgeItemFileUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/file`,
  knowledgeItemThumbnailUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/thumbnail`,
  knowledgeExtracted: (id: string) => get<{ contents: ExtractedContent[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/extracted`),
  knowledgeIngestStreamUrl: (id: string) => `/api/knowledge/items/${encodeURIComponent(id)}/ingest/stream`,
  knowledgeItemGraph: (id: string) => get<KnowledgeIngestGraph>(`/api/knowledge/items/${encodeURIComponent(id)}/graph`),
  knowledgeIntents: () => get<{ intents: KnowledgeIntent[] }>('/api/knowledge/intents'),
  upsertKnowledgeIntent: (body: Omit<KnowledgeIntent, 'id'> & { id?: string }) =>
    post<{ intents: KnowledgeIntent[]; id: string }>('/api/knowledge/intents', body),
  updateKnowledgeIntent: (id: string, body: Pick<KnowledgeIntent, 'enabled'> | Pick<KnowledgeIntent, 'propose_skill'>) =>
    patch<{ intent: KnowledgeIntent }>(`/api/knowledge/intents/${encodeURIComponent(id)}`, body),
  deleteKnowledgeIntent: (id: string) => del(`/api/knowledge/intents/${encodeURIComponent(id)}`),
  knowledgeIntentOutcomes: (id: string) =>
    get<{ intent: KnowledgeIntent; outcomes: IntentOutcome[] }>(`/api/knowledge/intents/${encodeURIComponent(id)}/outcomes`),
  runKnowledgeIntent: (id: string) =>
    post<{ recorded: number; matched: number; new: number; errors: number; evaluated: number; outcomes: IntentOutcome[] }>(`/api/knowledge/intents/${encodeURIComponent(id)}/run`, {}),
  generateSkillFromIntent: (id: string) =>
    post<{ skill: string; description: string }>(`/api/knowledge/intents/${encodeURIComponent(id)}/generate-skill`, {}),
  knowledgeItemIntents: (id: string) =>
    get<{ outcomes: IntentOutcome[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/intents`),
  createKnowledgeItem: (body: Record<string, unknown>) => post<KnowledgeItem>('/api/knowledge/items', body),
  updateKnowledgeItem: (id: string, body: Record<string, unknown>) => patch<{ ok: boolean }>(`/api/knowledge/items/${encodeURIComponent(id)}`, body),
  deleteKnowledgeItem: (id: string) => del(`/api/knowledge/items/${encodeURIComponent(id)}`),
  knowledgeProviders: () => get<{ providers: Array<{ name: string; display_name: string; always_on: boolean; kind: string }> }>('/api/knowledge/providers').then((d) => d.providers),
  knowledgeSources: () => get<SourcesResponse>('/api/knowledge/sources'),
  createKnowledgeSource: (body: {
    name: string; provider: string; spec: Record<string, unknown>
    enrichment?: string; poll_interval_secs?: number; budget?: Record<string, unknown>
  }) => post<{ source: WatchedSource }>('/api/knowledge/sources', body),
  updateKnowledgeSource: (id: string, body: {
    name?: string; enabled?: boolean; enrichment?: string; poll_interval_secs?: number
    spec?: Record<string, unknown>; budget?: Record<string, unknown>
  }) => patch<{ source: WatchedSource }>(`/api/knowledge/sources/${encodeURIComponent(id)}`, body),
  previewKnowledgeSource: (body: { provider: string; spec: Record<string, unknown>; budget?: Record<string, unknown> }) =>
    post<SourcePreviewResult>('/api/knowledge/sources/preview', body),
  knowledgeSourceRecipes: (url?: string) =>
    get<SourceRecipesResponse>(
      url ? `/api/knowledge/source-recipes?url=${encodeURIComponent(url)}` : '/api/knowledge/source-recipes',
    ),
  knowledgeTags: () => get<{ tags: string[] }>('/api/knowledge/tags').then((d) => d.tags),
  knowledgeCollections: () =>
    get<{ collections: KnowledgeCollection[] }>('/api/knowledge/collections').then((d) => d.collections),
  knowledgeLibraryHome: (limit?: number) =>
    get<KnowledgeLibraryHome>(`/api/knowledge/library-home${limit ? `?limit=${limit}` : ''}`),
  createKnowledgeCollection: (body: { name: string; kind?: 'manual' | 'smart'; query?: string; icon?: string }) =>
    post<{ ok: boolean; collection: KnowledgeCollection }>('/api/knowledge/collections', body),
  updateKnowledgeCollection: (id: string, body: { name?: string; kind?: 'manual' | 'smart'; query?: string; icon?: string; position?: number }) =>
    patch<{ ok: boolean; collection: KnowledgeCollection }>(`/api/knowledge/collections/${encodeURIComponent(id)}`, body),
  deleteKnowledgeCollection: (id: string) =>
    del(`/api/knowledge/collections/${encodeURIComponent(id)}`),
  knowledgeCollectionItems: (id: string, limit = 50) =>
    get<{ collection: KnowledgeCollection; items: KnowledgeItem[]; count: number }>(`/api/knowledge/collections/${encodeURIComponent(id)}/items?limit=${limit}`),
  addToKnowledgeCollection: (id: string, itemIds: string[]) =>
    post<{ ok: boolean; added: string[]; missing: string[] }>(`/api/knowledge/collections/${encodeURIComponent(id)}/items`, { item_ids: itemIds }),
  removeFromKnowledgeCollection: (id: string, itemId: string) =>
    del(`/api/knowledge/collections/${encodeURIComponent(id)}/items/${encodeURIComponent(itemId)}`),
  setKnowledgeReadState: (id: string, state: 'unread' | 'reading' | 'read') =>
    post<{ ok: boolean; read_state: string }>(`/api/knowledge/items/${encodeURIComponent(id)}/read-state`, { state }),
  setKnowledgeFavorited: (id: string, value: boolean) =>
    post<{ ok: boolean; favorited: boolean }>(`/api/knowledge/items/${encodeURIComponent(id)}/favorite`, { value }),
  knowledgeAnnotations: (id: string) =>
    get<{ annotations: KnowledgeAnnotation[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/annotations`).then((d) => d.annotations),
  createKnowledgeAnnotation: (id: string, body: { quote: string; occurrence: number; note?: string }) =>
    post<{ ok: boolean; annotation: KnowledgeAnnotation }>(`/api/knowledge/items/${encodeURIComponent(id)}/annotations`, body),
  deleteKnowledgeAnnotation: (annotationId: string) =>
    del(`/api/knowledge/annotations/${encodeURIComponent(annotationId)}`),
  knowledgeDuplicates: (id: string) =>
    get<{ duplicates: KnowledgeDuplicate[] }>(`/api/knowledge/items/${encodeURIComponent(id)}/duplicates`).then((d) => d.duplicates),
  mergeKnowledgeItems: (keepId: string, mergeId: string) =>
    post<KnowledgeMergeResult>(`/api/knowledge/items/${encodeURIComponent(keepId)}/merge`, { merge_id: mergeId, confirm: true }),
  knowledgeItemSections: (id: string) =>
    get<{ sections: KnowledgeSection[]; length: number }>(`/api/knowledge/items/${encodeURIComponent(id)}/sections`),
  knowledgeRestructurePreview: (id: string, verb: string, params: Record<string, unknown>) =>
    post<KnowledgeRestructurePreview>(
      `/api/knowledge/items/${encodeURIComponent(id)}/restructure/${encodeURIComponent(verb)}`,
      params,
    ),
  knowledgeRestructureApply: (
    id: string, verb: string, params: Record<string, unknown>, token: string, relink = true,
  ) =>
    post<KnowledgeRestructureResult>(
      `/api/knowledge/items/${encodeURIComponent(id)}/restructure/${encodeURIComponent(verb)}`,
      { ...params, confirm: true, token, relink },
    ),
  knowledgeRestructureUndoable: () =>
    get<{ undoable: KnowledgeUndoEntry[] }>('/api/knowledge/restructure/undo').then((d) => d.undoable),
  knowledgeRestructureUndo: (token: string) =>
    post<{ ok: boolean; verb: string; item_id: string; summary: string }>('/api/knowledge/restructure/undo', { token }),
  knowledgeBulk: (op: KnowledgeBulkOp, itemIds: string[], args?: Record<string, unknown>) =>
    post<KnowledgeBulkResult>('/api/knowledge/bulk', { op, item_ids: itemIds, ...(args ?? {}) }),
  artifactExtractedText: (slug: string) =>
    get<{ slug: string; text: string; truncated: boolean }>(
      `/api/artifacts/${encodeURIComponent(slug)}/extract`),
  knowledgeTagTree: () =>
    get<{ tags: KnowledgeTag[] }>('/api/knowledge/tag-tree').then((d) => d.tags),
  renameKnowledgeTag: (id: number, body: { name?: string; parent_id?: number | null }) =>
    patch<{ ok: boolean; tags: KnowledgeTag[] }>(`/api/knowledge/tags/${id}`, body),
  mergeKnowledgeTag: (id: number, into: number) =>
    post<{ ok: boolean; moved: number; already: number; tags: KnowledgeTag[] }>(
      `/api/knowledge/tags/${id}/merge`, { into, confirm: true }),
  deleteKnowledgeTag: (id: number) => del(`/api/knowledge/tags/${id}`),
  knowledgeConflicts: (limit = 100) =>
    get<{ conflicts: KnowledgeConflict[]; count: number }>(
      `/api/knowledge/conflicts?limit=${limit}`),
  knowledgeItemRelations: (id: string) =>
    get<{ outbound: KnowledgeItemRelation[]; inbound: KnowledgeItemRelation[] }>(
      `/api/knowledge/items/${encodeURIComponent(id)}/relations`),
  knowledgeEmbeddingStatus: () => get<{ enabled: boolean; available?: boolean; model?: string; total_items?: number; embedded_items?: number; stale_items?: number }>('/api/knowledge/embedding/status'),
  generateKnowledgeEmbeddings: (rebuild = false) => post<{ ok?: boolean; embedded?: number }>('/api/knowledge/embedding/generate', { rebuild }),
  ingestKnowledgeFile: async (
    file: File,
    onProgress?: (p: { loaded: number; total: number; pct: number }) => void,
  ): Promise<{ item_id?: string; type?: string; status: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    if (await needsChunked(file)) {
      return chunkedUpload(file, { target: 'knowledge', onProgress })
    }
    const fd = new FormData(); fd.append('file', file)
    const r = await fetch('/api/knowledge/ingest', { method: 'POST', headers: { ...SK }, body: fd })
    if (!r.ok) throw new Error(await errText(r))
    return r.json()
  },

  inbox: (kind?: string, mine = false) => {
    const query = new URLSearchParams()
    if (kind) query.set('kind', kind)
    if (mine) query.set('mine', '1')
    return get<InboxItem[]>(`/api/inbox${query.size ? `?${query}` : ''}`)
  },
  inboxKinds: () => get<{ kinds: InboxKindCount[] }>('/api/inbox/kinds').then((d) => d.kinds),
  markInboxSeen: (body: { ids?: string[]; kind?: string } = {}) =>
    post<{ ok: boolean; seen: number }>('/api/inbox/seen', body),
  createInboxNote: (text: string) =>
    post<{ ok: boolean; id: string; item: InboxItem }>('/api/inbox/notes', { text }),
  inboxStatus: () => get<InboxStatus>('/api/inbox/status'),
  inboxProviders: () => get<{ providers: InboxProvider[] }>('/api/inbox/providers').then((d) => d.providers),
  updateInboxItem: (id: string, body: Record<string, unknown>) => put<InboxItem>(`/api/inbox/${encodeURIComponent(id)}`, body),
  restoreInboxItem: (id: string) => post<InboxItem>(`/api/inbox/${encodeURIComponent(id)}/restore`),
  applyInboxProposal: (id: string, edited?: InboxProposal) =>
    post<InboxProposalApplyResult>(
      `/api/inbox/${encodeURIComponent(id)}/apply`,
      edited ? { proposal: edited } : {},
    ),
  draftInboxReply: (id: string) => post<InboxItem>(`/api/inbox/${encodeURIComponent(id)}/draft`),
  digestInboxChannel: (channelId: string, hours = 4) =>
    get<InboxItem>(`/api/inbox/digest?channel_id=${encodeURIComponent(channelId)}&hours=${hours}`),
  sendInboxReply: (id: string, text: string) => post<{ ok: boolean; delivered_to_session?: boolean }>('/api/inbox/send', { id, text }),
  openInboxItem: (id: string) => post<{ ok: boolean }>(`/api/inbox/${encodeURIComponent(id)}/open`),
  favoriteInboxItem: (id: string, favorited: boolean) =>
    post<{ ok: boolean; favorited: boolean }>(`/api/inbox/${encodeURIComponent(id)}/favorite`, { favorited }),
  dismissAllInbox: () => post<{ ok: boolean; dismissed: number }>('/api/inbox/dismiss-all'),
  clearReviewedInboxProposals: () => del('/api/inbox/proposals/reviewed'),
  restartInbox: () => post<{ ok: boolean; error?: string }>('/api/inbox/restart'),
  inboxSettings: () => get<{ settings: InboxSettings }>('/api/inbox/settings').then((d) => d.settings),
  saveInboxSettings: (s: Partial<InboxSettings>) => put<{ settings: InboxSettings }>('/api/inbox/settings', s),

  auditEvents: (opts: { limit?: number; cursor?: string; filters?: AuditFilters } = {}) => {
    const q = new URLSearchParams({ limit: String(opts.limit ?? 50) })
    if (opts.cursor) q.set('cursor', opts.cursor)
    for (const [k, v] of Object.entries(opts.filters ?? {})) if (v) q.set(k, v)
    return get<AuditPage>(`/api/security/audit?${q}`)
  },
  auditVerify: (full = false) => get<SelVerify>(`/api/security/audit/verify${full ? '?full=1' : ''}`),
  computerUseLiveView: () => get<ComputerUseLiveView>('/api/computer-use/live-view'),
  selRotate: () => post<{ rotated: boolean; entries_before: number; entries_after: number; archive_path: string }>('/api/sel/rotate'),
  sessionArchives: () => get<{ archives: SessionArchive[] }>('/api/session/archive').then((d) => d.archives),
  sessionArchiveRead: (name: string) =>
    fetch(`/api/session/archive/${encodeURIComponent(name)}`, { headers: { ...SK } })
      .then(async (r) => { if (!r.ok) throw await apiError(r); return r.text() }),
  projectExportUrl: (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}/export`,
  projectImport: (file: File, opts: { preview?: boolean } = {}) => {
    const fd = new FormData(); fd.append('file', file)
    const qs = opts.preview ? '?preview=1' : ''
    return fetch(`/api/projects/import${qs}`, { method: 'POST', headers: { ...SK }, body: fd }).then(j<ProjectImportResult>)
  },
  updateCheck: () => get<UpdateCheck>('/api/update/check'),
  changelog: () => get<{ content: string }>('/api/changelog').then((d) => d.content),
  applyUpdate: () => post<UpdateActionResult>('/api/update'),
  rollbackUpdate: () => post<UpdateActionResult>('/api/update', { action: 'rollback' }),
  cancelUpdate: () => post<{ ok?: boolean }>('/api/update/cancel'),
  setAutoUpdate: (enabled: boolean) => post<{ ok?: boolean }>('/api/update/auto', { enabled }),
  setUpdateDevMode: (enabled: boolean) => post<{ ok?: boolean }>('/api/update/dev-mode', { enabled }),
  restartProbe: () => post<{ ok: boolean; running_agents: number; sessions: number }>('/api/system/restart?probe=1'),
  restartGateway: () => post<{ ok?: boolean; status?: string; error?: string }>('/api/system/restart'),

  notificationSettings: () => get<{ settings: NotificationSettings }>('/api/notifications/settings').then((d) => d.settings),
  saveNotificationSettings: (s: Partial<NotificationSettings>) => put<{ settings: NotificationSettings }>('/api/notifications/settings', s),
  notificationRules: () => get<NotificationRulesDoc>('/api/notifications/rules'),
  saveNotificationRules: (body: { rules?: Record<string, NotificationRulePatch | null>; digest?: { schedule?: string } }) =>
    put<NotificationRulesDoc & { ok: boolean }>('/api/notifications/rules', body),
  proactiveDigest: () => get<TriageDigestView>('/api/proactive/digest'),
  proactiveReply: (runId: string, text: string) =>
    post<TriageReplyResult>('/api/proactive/digest/reply', { run_id: runId, text }),
  proactiveInstall: (cron?: string) =>
    post<{ ok: boolean; created: boolean; schedule: TriageSchedule }>(
      '/api/proactive/install', cron ? { cron } : {}),
  decisionJournal: (status?: string, domain?: string) => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (domain) p.set('domain', domain)
    const qs = p.toString()
    return get<DecisionJournalView>(`/api/knowledge/decisions${qs ? `?${qs}` : ''}`)
  },
  approvalRules: () =>
    get<{ rules: ApprovalRuleRow[]; unreadable: string[] }>('/api/memory/approval-rules'),
  saveApprovalRule: (body: { pattern: string; verdict: 'approve' | 'deny'; scope?: string; expires_at?: string | null; send_capable?: boolean }) =>
    post<{ ok: boolean; rule: ApprovalRuleRow }>('/api/memory/approval-rules', body),
  revokeApprovalRule: (key: string) => del(`/api/memory/approval-rules/${encodeURIComponent(key)}`),
  memorySettings: () => get<MemorySettings>('/api/memory/settings'),
  saveMemorySettings: (s: Partial<MemorySettings>) => put<MemorySettings>('/api/memory/settings', s),
  memoryVolunteerStats: (windowDays?: number) =>
    get<VolunteerStats>(`/api/memory/volunteer-stats${windowDays ? `?window_days=${windowDays}` : ''}`),
  memoryStats: () => get<MemoryStats>('/api/memory/stats'),
  memoryVaultStatus: () => get<MemoryVaultStatus>('/api/memory/vault'),
  syncMemoryVault: () => post<MemoryVaultSyncResult>('/api/memory/vault/sync', {}),
  dailyDigests: (rebuild = false) =>
    get<{ digests: DailyDigest[] }>(`/api/memory/daily-digests${rebuild ? '?rebuild=1' : ''}`).then((d) => d.digests),
  memorySemantic: () => get<{ entries: SemanticEntry[] }>('/api/memory/semantic').then((d) => d.entries),
  writeSemantic: (key: string, value: unknown) => put<{ ok?: boolean }>('/api/memory/semantic', { key, value }),
  deleteSemantic: (key: string) => del(`/api/memory/semantic/${encodeURIComponent(key)}`),
  memoryEpisodic: (opts: { offset?: number; limit?: number; tags?: string } = {}) =>
    get<{ entries: EpisodicEntry[] }>(`/api/memory/episodic?limit=${opts.limit ?? 50}&offset=${opts.offset ?? 0}${opts.tags ? `&tags=${encodeURIComponent(opts.tags)}` : ''}`).then((d) => d.entries),
  searchEpisodic: (q: string, tags?: string) =>
    get<{ entries: EpisodicEntry[] }>(`/api/memory/episodic/search?q=${encodeURIComponent(q)}${tags ? `&tags=${encodeURIComponent(tags)}` : ''}`).then((d) => d.entries),
  deleteEpisodic: (id: string) => del(`/api/memory/episodic/${encodeURIComponent(id)}`),
  memoryEvents: (opts: { offset?: number; limit?: number } = {}) =>
    get<{ events: MemoryEvent[] }>(`/api/memory/events?limit=${opts.limit ?? 50}&offset=${opts.offset ?? 0}`).then((d) => d.events),
  undoMemoryEvent: (eventId: number) =>
    post<{ ok: boolean; message: string }>(`/api/memory/events/${eventId}/undo`, {}),
  memoryContextPreview: (q: string) => get<MemoryContextPreview>(`/api/memory/context-preview?q=${encodeURIComponent(q)}`),
  consolidateMemory: (key: string) => post<{ ok?: boolean; key?: string; error?: string }>('/api/memory/consolidate', { key }),
  securityStats: () => get<SecurityStats>('/api/security/stats'),
  deniedCommands: () => get<DeniedCommands>('/api/security/denied-commands'),
  setUserDeniedCommands: (patterns: string[]) => patch<Record<string, any>>('/api/config/gideon', { path: 'security.denied_commands', value: patterns }),
  securityEgress: () => get<EgressPolicyConfig>('/api/security/egress'),
  credentialStore: () => get<CredentialStoreState>('/api/security/credentials'),
  migrateCredentialsToKeychain: () =>
    post<CredentialMoveResult>('/api/security/credentials/migrate', { confirm: true }),
  rollbackCredentialsToKeychain: () =>
    post<CredentialMoveResult>('/api/security/credentials/rollback', { confirm: true }),
  setCredentialKeychain: (on: boolean) =>
    patch<Record<string, any>>('/api/config/gideon', { path: 'security.credential_keychain', value: on }),
  secrets: (projectId = '') =>
    get<SecretsVaultState>(`/api/secrets${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`),
  putSecret: (name: string, value: string, projectId = '') =>
    post<SecretWriteResult>('/api/secrets', { name, value, project_id: projectId }),
  deleteSecret: (name: string, projectId = '') =>
    fetch(
      `/api/secrets?name=${encodeURIComponent(name)}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ''}`,
      { method: 'DELETE', headers: { ...SK } },
    ).then(j<SecretDeleteResult>),
  desktopState: () => get<DesktopStateWire>('/api/desktop/state'),
  setSecurityEgress: (cfg: EgressPolicyConfig) => patch<Record<string, any>>('/api/config/gideon', { path: 'security.egress', value: cfg }),
  projectionRules: () => get<Record<string, any>>('/api/config/gideon').then(
    (c) => ((c?.tools?.projection_rules ?? []) as ProjectionRule[])),
  setProjectionRules: (rules: ProjectionRule[]) => patch<Record<string, any>>('/api/config/gideon', { path: 'tools.projection_rules', value: rules }),
  toolsSavings: () => get<ToolsSavings>('/api/tools/savings'),
  toolGroups: () => get<ToolGroupsData>('/api/tools/groups'),
  setToolGroupsEnabled: (enabled: boolean) =>
    patch<Record<string, any>>('/api/config/gideon', { path: 'tools.groups_enabled', value: enabled }),
  recordFeedback: (body: FeedbackRecordBody) => post<{ ok: boolean; id: string; verdict: string }>('/api/feedback', body),
  feedbackTarget: (kind: FeedbackTargetKind, id: string) =>
    get<{ verdict: 'up' | 'down' | null; reason?: string }>(`/api/feedback/target/${kind}/${encodeURIComponent(id)}`),
  feedbackProducers: (windowDays?: number) =>
    get<FeedbackProducersResponse>(`/api/feedback/producers${windowDays ? `?window_days=${windowDays}` : ''}`),
  feedbackSnooze: (producer: FeedbackProducer) => post<{ ok: boolean }>('/api/feedback/producers/snooze', producer),
  feedbackClear: (producer: FeedbackProducer) => post<{ ok: boolean }>('/api/feedback/producers/clear', producer),
  investigate: (body: { kind: string; id: string; back_link?: string }) =>
    post<{ session_key: string; context: InvestigateOrigin & { snapshot: string; opening_prompt?: string } }>('/api/investigate', body),

  attachmentExtract: (path: string) => get<{ name: string; text: string }>(`/api/attachment-extract?path=${encodeURIComponent(path)}`),
  uploadFiles: async (
    files: File[],
    onProgress?: (fileIndex: number, p: { loaded: number; total: number; pct: number }) => void,
    signal?: AbortSignal,
  ): Promise<{ paths: string[]; error?: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    const paths: string[] = []
    const small: File[] = []
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      if (await needsChunked(f)) {
        const res = await chunkedUpload(f, { target: 'attachment', onProgress: (p) => onProgress?.(i, p), signal })
        if (res?.paths) paths.push(...res.paths)
      } else {
        small.push(f)
      }
    }
    if (small.length) {
      const fd = new FormData()
      small.forEach((f) => fd.append('file', f))
      const r = await fetch('/api/upload/file', { method: 'POST', headers: { ...SK }, body: fd, signal })
      const data = await j<{ paths: string[]; error?: string }>(r)
      if (data.paths) paths.push(...data.paths)
    }
    return { paths }
  },

  fileRoots: () => get<FileListResp>('/api/file-list'),
  fileList: (path: string) => get<FileListResp>(`/api/file-list?path=${encodeURIComponent(path)}`),
  fileRead: (path: string, resolve = false) => fetch(`/api/file-read?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`, { headers: { ...SK } }).then(async (r) => {
    if (!r.ok) throw await apiError(r)

    return { content: await r.text(), truncated: r.headers.get('X-Truncated') === 'true', binary: r.headers.get('X-Binary') === 'true' }
  }),
  fileWrite: (path: string, content: string) => post<{ ok: boolean }>('/api/file-write', { path, content }),
  fileCreate: (parent: string, name: string, kind: 'file' | 'dir', content?: string) =>
    post<{ ok: boolean; path: string; is_dir: boolean }>('/api/file-create', { path: parent, name, kind, content }),
  fileMove: (src: string, dest: string) => post<{ ok: boolean; path: string }>('/api/file-move', { src, dest }),
  fileDelete: (path: string) => post<{ ok: boolean }>('/api/file-delete', { path }),
  fileUpload: async (
    dir: string, files: File[],
    onProgress?: (fileIndex: number, p: { loaded: number; total: number; pct: number }) => void,
    signal?: AbortSignal,
  ): Promise<{ ok: boolean; paths?: string[]; error?: string }> => {
    const { needsChunked, chunkedUpload } = await import('./chunkedUpload')
    const paths: string[] = []
    const small: File[] = []
    try {
      for (let i = 0; i < files.length; i++) {
        const f = files[i]
        if (await needsChunked(f)) {
          const res = await chunkedUpload(f, { target: 'workspace', path: dir, onProgress: (p) => onProgress?.(i, p), signal })
          if (res?.paths) paths.push(...res.paths)
        } else {
          small.push(f)
        }
      }
      if (small.length) {
        const fd = new FormData()
        for (const f of small) fd.append('file', f, f.name)
        const r = await fetch(`/api/file-upload?path=${encodeURIComponent(dir)}`, { method: 'POST', headers: { ...SK }, body: fd, signal })
        const data = await r.json().catch(() => ({}))
        if (!r.ok) return { ok: false, error: data?.error || `HTTP ${r.status}` }
        if (data?.paths) paths.push(...data.paths)
      }
    } catch (e) {
      const { isAbortError } = await import('./chunkedUpload')
      if (isAbortError(e)) throw e
      return { ok: false, error: (e as Error).message }
    }
    return { ok: true, paths }
  },
  fileGitStatus: (path: string) => get<GitStatusResp>(`/api/file-git-status?path=${encodeURIComponent(path)}`),
  fileGitLog: (path: string, limit = 20) =>
    get<{ repoRoot: string; commits: { hash: string; subject: string; relative: string; author: string }[] }>(`/api/file-git-log?path=${encodeURIComponent(path)}&limit=${limit}`),
  fileGitCommit: (path: string, hash: string) =>
    get<{ repoRoot: string; hash: string; subject: string; diff: string; truncated?: boolean; found?: boolean }>(`/api/file-git-commit?path=${encodeURIComponent(path)}&hash=${encodeURIComponent(hash)}`),
  fileGitOriginal: (path: string) => get<{ content: string; exists: boolean; truncated?: boolean }>(`/api/file-git-original?path=${encodeURIComponent(path)}`),
  fileContentSearch: (path: string, q: string, include?: string) =>
    get<ContentSearchResp>(`/api/file-content-search?path=${encodeURIComponent(path)}&q=${encodeURIComponent(q)}${include ? `&include=${encodeURIComponent(include)}` : ''}`),
  fileSearch: (q: string, project?: string) =>
    get<{ results: { path: string; name: string; size: number; mtime: number }[]; root?: string }>(`/api/file-search?q=${encodeURIComponent(q)}${project ? `&project=${encodeURIComponent(project)}` : ''}`),
  fileComplete: (path: string, kind?: 'dir') =>
    get<{ suggestions: FsEntry[]; truncated: boolean }>(`/api/file-complete?path=${encodeURIComponent(path)}${kind ? `&kind=${kind}` : ''}`),
  browseDirs: (path?: string) =>
    get<{ path: string; parent: string; in_repo?: boolean; dirs: { name: string; path: string; is_repo?: boolean }[] }>(`/api/browse-dirs${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  createDir: (path: string) => post<{ ok: boolean; path: string }>('/api/create-dir', { path }),
  fileRawUrl: (path: string, resolve = false) => `/api/file-raw?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`,
  fileWatchUrl: (path: string, resolve = false) => `/api/file-watch?path=${encodeURIComponent(path)}${resolve ? '&resolve=1' : ''}`,
  configFsStreamUrl: () => `/api/config-fs/stream`,

  workflowDefs: (f?: { tag?: string; source?: string }) => {
    const qs = new URLSearchParams(Object.entries(f ?? {}).filter(([, v]) => v) as [string, string][]).toString()
    return get<{ defs: WorkflowDefSummary[]; total: number }>(`/api/workflows${qs ? `?${qs}` : ''}`)
  },
  workflowSurfacing: () =>
    get<{ defs: WorkflowSurfacingRow[]; total: number; findings: WorkflowSurfacingFinding[] }>(
      '/api/workflows/surfacing',
    ),
  workflowDef: (name: string) =>
    get<{ definition: WorkflowDef; provider: string }>(`/api/workflows/${encodeURIComponent(name)}`),
  saveWorkflowDef: (body: { name: string; root: WorkflowNode; description?: string; inputs?: Record<string, unknown>; tags?: string[]; metadata?: Record<string, unknown>; save?: boolean }) =>
    post<{ saved: boolean; definition?: WorkflowDef; valid: boolean; issues: Array<{ code: string; message: string; path?: string; severity?: string }>; levels?: string[][] }>('/api/workflows', body),
  publishWorkflowToA2A: (name: string, published: boolean) =>
    post<{ ok: boolean; name: string; a2a_published: boolean }>(`/api/workflows/${encodeURIComponent(name)}/a2a-publish`, { published }),
  deleteWorkflowDef: (name: string) => del(`/api/workflows/${encodeURIComponent(name)}`),

  workflowVersions: (name: string) =>
    get<{ versions: WorkflowVersionRow[]; pinned: number; maturity: WorkflowMaturity }>(
      `/api/workflows/${encodeURIComponent(name)}/versions`,
    ),
  workflowVersionDiff: (name: string, a: number, b: number) =>
    get<{ a: number; b: number; ops: WorkflowVersionOp[] }>(
      `/api/workflows/${encodeURIComponent(name)}/versions/diff?a=${a}&b=${b}`,
    ),
  repinWorkflowVersion: (name: string, version: number) =>
    post<{ ok: boolean; name: string; pinned: number }>(
      `/api/workflows/${encodeURIComponent(name)}/versions/repin`,
      { version },
    ),
  workflowLedger: (name: string) =>
    get<{ name: string; runs: WorkflowLedgerRow[]; total: number }>(
      `/api/workflows/${encodeURIComponent(name)}/ledger`,
    ),
  refineWorkflow: (name: string) =>
    post<{ run_id?: string; status?: string }>(`/api/workflows/${encodeURIComponent(name)}/refine`, {}),

  workflowRuns: (f?: { workflow?: string; status?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams(
      Object.entries(f ?? {}).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)]),
    ).toString()
    return get<{ runs: WorkflowRunSummary[]; total: number; limit: number; offset: number }>(`/api/workflows/runs${qs ? `?${qs}` : ''}`)
  },
  startWorkflowRun: (body: { name: string; inputs?: Record<string, unknown>; mode?: 'blocking' | 'background'; project_id?: string; idempotency_key?: string }) =>
    post<{ run_id: string; status: string; blocking?: boolean; needs_input?: WorkflowContinuation[] }>('/api/workflows/runs', body),
  workflowRun: (id: string) => get<WorkflowRunDetailData>(`/api/workflows/runs/${encodeURIComponent(id)}`),
  setWorkflowRunPolicyOverrides: (id: string, overrides: Record<string, unknown>) =>
    put<{ run_id: string; status: string; policy_overrides: Record<string, unknown> }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/policy-overrides`, overrides),
  confirmWorkflowRun: (id: string, body: { verb: 'approve' | 'reject' | 'skip' | 'quit'; resume_token?: string; note?: string }) =>
    post<{ ok?: boolean; verb?: string; approved?: boolean; resumed?: boolean; still_pending?: boolean; code?: string; message?: string }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/confirm`,
      body,
    ),
  workflowRunOutput: (id: string, nodeId: string) =>
    get<{ run_id: string; node_id: string; instance_path: string; state: string; output: unknown }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/outputs/${encodeURIComponent(nodeId)}`),
  workflowRunNodeInspect: (runId: string, nodeId: string) =>
    get<NodeInspect>(
      `/api/workflows/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/inspect`),
  workflowContinuations: (id: string) =>
    get<{ continuations: WorkflowContinuation[] }>(`/api/workflows/runs/${encodeURIComponent(id)}/continuations`),
  workflowRunWorkspace: (id: string) =>
    get<WorkflowWorkspaceReview>(`/api/workflows/runs/${encodeURIComponent(id)}/workspace`),
  workflowRunOutbox: (id: string) =>
    get<{ files: WorkflowOutboxEntry[] }>(`/api/workflows/runs/${encodeURIComponent(id)}/outbox`),
  workflowRunIntrospect: (id: string) =>
    get<WorkflowIntrospection>(`/api/workflows/runs/${encodeURIComponent(id)}/introspect`),
  workflowRunLedgerRails: (id: string) =>
    get<WorkflowLedgerRails>(`/api/workflows/runs/${encodeURIComponent(id)}/ledger-rails`),
  workflowRunDeliverable: (id: string) =>
    get<WorkflowRunDeliverable>(`/api/workflows/runs/${encodeURIComponent(id)}/deliverable`),
  workflowRunDropStatus: (id: string) =>
    get<WorkflowDropStatus>(`/api/workflows/runs/${encodeURIComponent(id)}/drop`),
  workflowRunDrop: (id: string, files: File[], confirm = false) => {
    const body = new FormData()
    for (const f of files) body.append('file', f)
    return fetch(
      `/api/workflows/runs/${encodeURIComponent(id)}/drop${confirm ? '?confirm=true' : ''}`,
      { method: 'POST', headers: { ...SK }, body },
    ).then(j<WorkflowDropStatus>)
  },
  editWorkflowRun: (id: string, body: { ops: Array<Record<string, unknown>>; expect_version?: number; confirm_cascade?: boolean; preview_only?: boolean }) =>
    post<{ ok?: boolean; queued?: boolean; preview: WorkflowCascadePreview; issues: Array<{ code: string; message: string; node_id?: string }> }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/edit`, body),
  cancelWorkflowRun: (id: string) => post<{ run_id: string; cancel_requested: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/cancel`),
  deleteWorkflowRun: (id: string) => del(`/api/workflows/runs/${encodeURIComponent(id)}`),
  pauseWorkflowRun: (id: string) => post<{ run_id: string; pause_requested: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/pause`),
  startWorkflowDraft: (id: string) => post<{ run_id: string; status: string }>(`/api/workflows/runs/${encodeURIComponent(id)}/start`),
  steerWorkflowRun: (id: string, body: { text: string }) =>
    post<{ ok?: boolean; run_id?: string; queued?: number; error?: { code: string; message: string } }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/steer`, body),
  workflowSteering: (id: string) =>
    get<{ run_id: string; pending: Array<{ text: string; queued_at: string }>; count: number }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/steering`),
  workflowReview: (id: string) =>
    get<WorkflowReviewPayload>(`/api/workflows/runs/${encodeURIComponent(id)}/review`),
  workflowReviewTriage: (
    id: string,
    body: { decisions: Array<{ key: string; outcome: 'accept' | 'reject'; reason?: string }>; dry_run?: boolean },
  ) =>
    post<WorkflowTriageResult>(`/api/workflows/runs/${encodeURIComponent(id)}/review/triage`, body),
  resumeWorkflowRun: (id: string, body: { answer?: unknown; resume_token?: string; always_allow?: boolean }) =>
    post<{ ok?: boolean; approved?: boolean; node_id?: string; resumed?: boolean }>(`/api/workflows/runs/${encodeURIComponent(id)}/resume`, body),
  rewindWorkflowRun: (id: string, body: { node_id: string; redo_effects?: boolean; force?: boolean; confirm_cascade?: boolean }) =>
    post<{ ok?: boolean; preview: WorkflowCascadePreview }>(`/api/workflows/runs/${encodeURIComponent(id)}/rewind`, body),
  workflowRunFrom: (id: string, body: { node_id: string; confirm_cascade?: boolean }) =>
    post<{ ok?: boolean; preview: WorkflowCascadePreview }>(`/api/workflows/runs/${encodeURIComponent(id)}/run-from`, body),
  forkWorkflowRun: (id: string, body?: { checkpoint_id?: string; note?: string }) =>
    post<{ child_run_id: string; fork_axis: string; shared_axes: string[]; isolation_notes: string[] }>(
      `/api/workflows/runs/${encodeURIComponent(id)}/fork`, body ?? {}),
  workflowAudit: (dryRun = true) =>
    get<{ healthy: boolean; dry_run: boolean; runs_scanned: number; counts: Record<string, number>; findings: Array<{ kind: string; run_id: string; detail: string; heal: string; healed: boolean }> }>(
      `/api/workflows/audit?dry_run=${dryRun ? 'true' : 'false'}`),
  workflowManifest: () => get<WorkflowManifest>('/api/workflows/manifest'),
  workflowAttention: () => get<{ scopes: AttentionScope[] }>('/api/workflows/attention'),
  workflowRunStreamUrl: (id: string) => `/api/workflows/runs/${encodeURIComponent(id)}/events`,

  artifacts: (f?: { tag?: string; kind?: string; q?: string; source?: string; source_path?: string }) => {
    const qs = new URLSearchParams(Object.entries(f ?? {}).filter(([, v]) => v) as [string, string][]).toString()
    return get<{ artifacts: Artifact[] }>(`/api/artifacts${qs ? `?${qs}` : ''}`).then((d) => d.artifacts)
  },
  artifact: (slug: string) => get<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}`),
  pinnedArtifacts: () => get<{ pins: PinnedArtifact[] }>('/api/artifacts/pinned'),
  pinArtifact: (slug: string, pinned: boolean, runId = '') =>
    post<{ ok: boolean; pinned: boolean; pins: PinnedArtifact[] }>(
      `/api/artifacts/${encodeURIComponent(slug)}/pin`,
      { pinned, run_id: runId },
    ),
  artifactExists: (slug: string) =>
    get<{ exists: boolean }>(`/api/artifacts/${encodeURIComponent(slug)}?probe=1`).then((d) => d.exists),
  createArtifact: (body: { name: string; content: string; kind?: string; source?: string; source_path?: string; description?: string; tags?: string[]; slug?: string; project_id?: string }) =>
    post<Artifact>('/api/artifacts', body),
  updateArtifact: (slug: string, body: ArtifactUpdate) => patch<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}`, body),
  deleteArtifact: (slug: string) => del(`/api/artifacts/${encodeURIComponent(slug)}`),
  regenerateArtifactImage: (slug: string, body: { session?: string; prompt?: string }) =>
    post<{ ok: boolean; slug: string }>(`/api/artifacts/${encodeURIComponent(slug)}/regenerate`, body),
  artifactVersions: (slug: string) => get<{ slug: string; versions: number[] }>(`/api/artifacts/${encodeURIComponent(slug)}/versions`),
  artifactVersion: (slug: string, n: number) => get<Artifact>(`/api/artifacts/${encodeURIComponent(slug)}/versions/${n}`),
  artifactEvents: (slug: string) => get<{ slug: string; events: ArtifactEvent[] }>(`/api/artifacts/${encodeURIComponent(slug)}/events`),

  artifactModel: (slug: string) => get<DocumentModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactModel: (slug: string, version: number, model: DocumentModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>).then((result) => publishArtifactModelSaved(slug, version, result)),
  artifactSheetModel: (slug: string) => get<SheetModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactSheetModel: (slug: string, version: number, model: SheetModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>).then((result) => publishArtifactModelSaved(slug, version, result)),
  artifactDeckModel: (slug: string) => get<DeckModelResponse>(`/api/artifacts/${encodeURIComponent(slug)}/model`),
  saveArtifactDeckModel: (slug: string, version: number, model: DeckModelJson) =>
    fetch(`/api/artifacts/${encodeURIComponent(slug)}/model`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'If-Match': String(version), ...SK },
      body: JSON.stringify({ model }),
    }).then(j<{ slug: string; version: number; mime: string }>).then((result) => publishArtifactModelSaved(slug, version, result)),

  deployedArtifacts: () => get<{ deployments: ArtifactDeployment[] }>('/api/artifacts/deployed').then((d) => d.deployments),
  deployArtifact: (slug: string, body?: { entry?: string }) =>
    post<{ ok: boolean; deployment: ArtifactDeployment }>(`/api/artifacts/${encodeURIComponent(slug)}/deploy`, body ?? {}),
  teardownArtifact: (slug: string) =>
    del(`/api/artifacts/${encodeURIComponent(slug)}/deploy`),

  dashboardViews: () => get<{ views: DashboardView[] }>('/api/dashboard/views').then((d) => d.views),
  createView: (body: { name: string; icon?: string }) => post<{ view: DashboardView }>('/api/dashboard/views', body).then((d) => d.view),
  updateView: (id: string, body: Partial<Pick<DashboardView, 'name' | 'icon' | 'nav_pinned'>>) =>
    put<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(id)}`, body).then((d) => d.view),
  deleteView: (id: string) => del(`/api/dashboard/views/${encodeURIComponent(id)}`),
  pinTile: (viewId: string, body: { slug: string; size?: TileSize }) =>
    post<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles`, body).then((d) => d.view),
  resolveTile: (viewId: string, body: { ref: string; keep: boolean }) =>
    post<{ view: DashboardView }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/resolve`, body).then((d) => d.view),

  surfaceOverlays: () => get<SurfaceOverlayPayload>('/api/surfaces/overlays'),

  bindTile: (viewId: string, body: { ref: string } & Partial<TileRefresh>) =>
    put<{ tile: DashboardTile }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/binding`, body).then((d) => d.tile),
  refreshTile: (viewId: string, body: { ref: string; force?: boolean }) =>
    post<TileRefreshResult>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh`, body),
  tileRefreshRow: (viewId: string, ref: string) =>
    get<{ row: TileRefreshRow }>(`/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh?ref=${encodeURIComponent(ref)}`).then((d) => d.row),
  tileLedgerHref: (viewId: string, ref: string) =>
    `/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/refresh?ref=${encodeURIComponent(ref)}`,
  tileWidgetAction: (viewId: string, body: { ref: string; action: string; payload?: Record<string, unknown> }) =>
    post<{ ok: boolean; code?: string; message?: string; outcome?: string; violations?: string[][]; row?: TileRefreshRow }>(
      `/api/dashboard/views/${encodeURIComponent(viewId)}/tiles/action`, body),

  apps: () => get<{ apps: (AppSummary & { platform?: boolean })[] }>('/api/apps')
    .then((d) => d.apps.map((a) => (a.native ?? a.platform) ? { ...a, native: true } : a)),
  app: (name: string) => get<AppDetail>(`/api/apps/${encodeURIComponent(name)}`),
  installApp: (source: string, confirm = false) => _installReq('/api/apps', { source, confirm }),
  updateApp: (name: string, source: string, confirm = false) =>
    _installReq(`/api/apps/${encodeURIComponent(name)}/update`, { source, confirm }),
  enableApp: (name: string) => post<{ ok: boolean }>(`/api/apps/${encodeURIComponent(name)}/enable`),
  disableApp: (name: string) => post<{ ok: boolean }>(`/api/apps/${encodeURIComponent(name)}/disable`),
  uninstallApp: (name: string, force = false) =>
    del(`/api/apps/${encodeURIComponent(name)}${force ? '?force=1' : ''}`),
  removeApp: (name: string) => del(`/api/apps/${encodeURIComponent(name)}?remove=1`),
  appUninstallPreview: (name: string) =>
    get<{ name: string; dependencies: AppDepClassification[]; data?: AppDataFacts }>(`/api/apps/${encodeURIComponent(name)}/uninstall-preview`),
  appConfig: (name: string) =>
    get<{ name: string; config: Record<string, unknown>; schema: Record<string, unknown>; _secret_set?: string[] }>(`/api/apps/${encodeURIComponent(name)}/config`),
  saveAppConfig: (name: string, config: Record<string, unknown>) =>
    put<{ ok: boolean; config: Record<string, unknown> }>(`/api/apps/${encodeURIComponent(name)}/config`, config),
  appCatalog: () => get<AppCatalog>('/api/apps/catalog'),
  appSources: () => get<{ sources: string[] }>('/api/apps/sources').then((d) => d.sources),
  addAppSource: (url: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/sources', { url }),
  removeAppSource: (url: string) => del(`/api/apps/sources?url=${encodeURIComponent(url)}`),
  addLocalAppSource: (path: string) => post<{ ok: boolean; sources: string[] }>('/api/apps/local-sources', { path }),
  removeLocalAppSource: (path: string) => del(`/api/apps/local-sources?path=${encodeURIComponent(path)}`),
}
