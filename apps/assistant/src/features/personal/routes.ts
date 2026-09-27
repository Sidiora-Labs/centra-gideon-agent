import { createShellRoute, type ShellDestination, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'

export type PersonalSpace = 'ideas' | 'goals' | 'identity' | 'learning' | 'companion' | 'memory' | 'health' | 'journal'
export type PersonalRecordKind = 'idea' | 'human-goal' | 'goal-plan' | 'identity-story' | 'learning-capture' | 'learning-review' | 'companion-item' | 'memory-fact' | 'health-measurement' | 'journal-entry'

export const PERSONAL_SPACES: readonly Readonly<{ id: PersonalSpace; label: string; shell: ShellDestination; placement: string }>[] = Object.freeze([
  { id: 'ideas', label: 'Ideas', shell: 'ideas', placement: 'ideas' },
  { id: 'goals', label: 'Goals', shell: 'goals', placement: 'goals' },
  { id: 'identity', label: 'Identity', shell: 'ideas', placement: 'capabilities/identity/autobiography' },
  { id: 'learning', label: 'Learning', shell: 'ideas', placement: 'learning' },
  { id: 'companion', label: 'Companion', shell: 'ideas', placement: 'companion' },
  { id: 'memory', label: 'Memory', shell: 'ideas', placement: 'capabilities/wellbeing/memory' },
  { id: 'health', label: 'Health', shell: 'ideas', placement: 'capabilities/wellbeing/overview' },
  { id: 'journal', label: 'Journal', shell: 'ideas', placement: 'capabilities/knowledge/journals' },
])

export function personalSpaceRoute(space: PersonalSpace, options: {
  record?: Readonly<{ kind: PersonalRecordKind; id: string }>
  returnTo?: ShellReturnContext
} = {}): ShellRoute {
  const destination = PERSONAL_SPACES.find(item => item.id === space)!
  return createShellRoute(destination.shell, {
    view: options.record ? 'detail' : space === 'ideas' || space === 'goals' ? 'list' : 'workspace',
    placement: { id: destination.placement },
    record: options.record,
    returnTo: options.returnTo,
  })
}

export function personalDetailRoute(space: PersonalSpace, kind: PersonalRecordKind, id: string,
  current: ShellRoute): ShellRoute {
  return personalSpaceRoute(space, {
    record: { kind, id },
    returnTo: {
      destination: current.destination,
      record: current.record,
      placement: current.placement,
      sessionId: current.sessionId,
      selectionId: current.record?.id,
    },
  })
}
