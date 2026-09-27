import type { ModuleDefinition } from '../../shared/shell/webModules.web'
import type { OwnerScope } from '../../shared/auth.web'
import { GatewayError } from '../../shared/transport.web'
import type { RouteAvailability } from '../../shared/shell/routeState.web'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import { createPersonalClient } from './client'

const PLACEMENTS = [
  'ideas', 'goals', 'learning', 'companion',
  'capabilities/knowledge/ideas', 'capabilities/knowledge/journals',
  'capabilities/identity/autobiography', 'capabilities/identity/twin',
  'capabilities/identity/goals', 'capabilities/identity/goal-plans',
  'capabilities/identity/progress', 'capabilities/identity/fidelity',
  'capabilities/identity/continuity', 'capabilities/identity/bundles',
  'capabilities/identity/recipes', 'capabilities/identity/guarded-recipes',
  'capabilities/identity/lifecycle', 'capabilities/wellbeing/overview',
  'capabilities/wellbeing/measurements', 'capabilities/wellbeing/labs',
  'capabilities/wellbeing/body-composition', 'capabilities/wellbeing/eyes',
  'capabilities/wellbeing/epigenetic', 'capabilities/wellbeing/lifestyle',
  'capabilities/wellbeing/consumption', 'capabilities/wellbeing/interventions',
  'capabilities/wellbeing/cognition', 'capabilities/wellbeing/memory',
  'capabilities/wellbeing/life', 'capabilities/wellbeing/genome',
  'capabilities/wellbeing/import', 'capabilities/wellbeing/exports',
  'capabilities/wellbeing/shared', 'capabilities/wellbeing/privacy',
  'capabilities/wellbeing/organizations',
] as const

function availability(error: unknown): RouteAvailability {
  if (error instanceof GatewayError) {
    if (error.status === 403) return 'denied'
    if (error.status === 404) return 'missing'
  }
  return 'unavailable'
}

async function resolve(scope: OwnerScope, route: ShellRoute): Promise<RouteAvailability> {
  const client = createPersonalClient(scope)
  const placement = route.placement?.id ?? (route.destination === 'goals' ? 'goals' : 'ideas')
  try {
    if (route.record) {
      const { kind, id } = route.record
      if (kind === 'idea') await client.readIdea(id)
      else if (kind === 'human-goal') await client.readGoal(id)
      else if (kind === 'goal-plan') await client.readGoalPlan(id)
      else if (kind === 'identity-story') await client.readIdentityStory(id)
      else if (kind === 'health-measurement') await client.readHealthMeasurement(id)
      else if (kind === 'memory-fact') await client.readMemoryFact(id)
      else return 'unavailable'
      return 'available'
    }
    if (placement === 'goals' || placement === 'capabilities/identity/goals') await client.readGoals()
    else if (placement === 'capabilities/identity/goal-plans') await client.readGoalPlans()
    else if (placement === 'ideas' || placement === 'capabilities/knowledge/ideas') await client.readIdeas()
    else if (placement === 'learning') {
      const result = await client.readLearningCaptures()
      if (result.state === 'unavailable') return 'unavailable'
    } else if (placement === 'companion') {
      const result = await client.readCompanion()
      if (result.state === 'unavailable') return 'unavailable'
    } else if (placement === 'capabilities/knowledge/journals') {
      const result = await client.readJournal(new Date().toISOString().slice(0, 10), Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
      if (result.state === 'unavailable') return 'unavailable'
    } else if (placement === 'capabilities/wellbeing/memory') await client.readMemory()
    else if (placement === 'capabilities/identity/autobiography') await client.readIdentityStories()
    else if (placement === 'capabilities/identity/twin') await client.readIdentityProfile()
    else if (placement === 'capabilities/wellbeing/overview' || placement === 'capabilities/wellbeing/measurements') await client.readHealthMeasurements()
    else if (placement.startsWith('capabilities/identity/') || placement.startsWith('capabilities/wellbeing/')) return 'unavailable'
    else return 'missing'
    return 'available'
  } catch (error) { return availability(error) }
  finally { client.dispose() }
}

export const personalModuleDefinitions: readonly ModuleDefinition[] = Object.freeze(PLACEMENTS.map(id => Object.freeze({
  id,
  mode: 'full' as const,
  matches: (route: ShellRoute) => route.placement?.id === id || (!route.placement && route.destination === id),
  resolve,
  load: () => import('./PersonalHome').then(module => ({ default: module.PersonalHome })),
})))
