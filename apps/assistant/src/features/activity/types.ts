import type { OwnerScope } from '../../shared/auth.web'
import type { ShellRecord, ShellRoute } from '../../shared/shell/shellRoutes'
import type { ChatDetail, ConversationState } from '../../shared/conversation/types'
import type {
  Artifact, ChatSession, InboxItem, NotificationItem, PendingApproval, ScheduleRun,
  TaskItem, WorkflowRunSummary,
} from '../../../../console/src/shared/data/api'

export type ActivitySourceKind =
  | 'task' | 'workflow_run' | 'trigger_run' | 'chat_session'
  | 'inbox_item' | 'approval' | 'notification' | 'artifact'

export type NativeId<K extends ActivitySourceKind> = string & { readonly __activitySource: K }

export type ActivityIdentity<K extends ActivitySourceKind = ActivitySourceKind> = Readonly<{
  ownerScopeKey: OwnerScope['cacheKey']
  sourceKind: K
  sourceId: NativeId<K>
  key: string
}>

export type ActivityRelatedIds = Readonly<{
  taskId?: NativeId<'task'>
  workflowRunId?: NativeId<'workflow_run'>
  triggerRunId?: NativeId<'trigger_run'>
  approvalId?: NativeId<'approval'>
  inboxItemId?: NativeId<'inbox_item'>
  artifactId?: NativeId<'artifact'>
  chatSessionId?: NativeId<'chat_session'>
  notificationId?: NativeId<'notification'>
}>

export type ActivityOutcome =
  | 'queued' | 'working' | 'paused' | 'blocked' | 'skipped' | 'cancelled'
  | 'failed' | 'waiting_input' | 'waiting_approval' | 'escalated'
  | 'complete' | 'stopping' | 'sent' | 'handled' | 'dismissed'
  | 'filtered' | 'seen' | 'notice' | 'acknowledged' | 'available' | 'unknown'

export type ActivityStatus = Readonly<{
  native: string | null
  outcome: ActivityOutcome
}>

export type ActivityDestination = Readonly<{
  ownerScopeKey: OwnerScope['cacheKey']
  permission: 'unchecked'
  route: ShellRoute & { record: ShellRecord }
}>

export type ActivityEntry<K extends ActivitySourceKind = ActivitySourceKind> = Readonly<{
  identity: ActivityIdentity<K>
  latestEventId: string | null
  occurredAt: string | number | null
  progressPercent: number | null
  status: ActivityStatus
  actionability: 'none' | 'open' | 'reply' | 'review'
  title: string
  summary: string | null
  related: ActivityRelatedIds
  destination: ActivityDestination
}>

export type ActivityUnavailable<K extends ActivitySourceKind = ActivitySourceKind> = Readonly<{
  availability: 'unavailable'
  sourceKind: K
  reason: 'missing_native_id'
}>

export type ActivityMapped<K extends ActivitySourceKind = ActivitySourceKind> =
  | Readonly<{ availability: 'available'; entry: ActivityEntry<K> }>
  | ActivityUnavailable<K>

export type ActivitySourceRecords = Readonly<{
  task: TaskItem
  workflow_run: WorkflowRunSummary
  trigger_run: ScheduleRun
  chat_session: ChatSession | ChatDetail | ConversationState
  inbox_item: InboxItem
  approval: PendingApproval
  notification: NotificationItem & { id?: string }
  artifact: Artifact
}>
