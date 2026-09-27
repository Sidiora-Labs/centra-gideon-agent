import { readOwnerSession, type OwnerScope } from '../../shared/auth.web'
import { createShellRoute, type ShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import { DESTINATIONS } from '../discovery/destinations'
import { sameStudioOwner, studioRecordRef, type StudioJobState, type StudioRecordRef } from './studioContracts'

export type StudioTarget = Readonly<{ area: 'creative' | 'media' | 'music' | 'experience'; view: string }>

const TARGETS: Readonly<Record<string, StudioTarget | null>> = Object.freeze({
  design: null,
  'capabilities/media/sketches': { area: 'media', view: 'sketches' },
  'capabilities/media/images': { area: 'media', view: 'images' },
  'capabilities/media/videos': { area: 'media', view: 'videos' },
  'capabilities/media/animations': { area: 'media', view: 'animation' },
  'capabilities/media/sprites': { area: 'media', view: 'sprites' },
  'capabilities/media/episodes': { area: 'media', view: 'episodes' },
  'capabilities/media/timelines': { area: 'media', view: 'timelines' },
  'capabilities/media/cleanup': { area: 'media', view: 'cleanup' },
  'capabilities/media/datasets': { area: 'media', view: 'datasets' },
  'capabilities/media/downloads': { area: 'media', view: 'downloads' },
  'capabilities/media/library': { area: 'media', view: 'library' },
  'capabilities/media/jobs': { area: 'media', view: 'jobs' },
  'capabilities/media/readiness': { area: 'media', view: 'readiness' },
  'capabilities/creative/ingredients': { area: 'creative', view: 'ingredients' },
  'capabilities/creative/boards': { area: 'creative', view: 'boards' },
  'capabilities/creative/universes': { area: 'creative', view: 'universes' },
  'capabilities/creative/authors': { area: 'creative', view: 'authors' },
  'capabilities/creative/works': { area: 'creative', view: 'works' },
  'capabilities/creative/stories': { area: 'creative', view: 'stories' },
  'capabilities/creative/series': { area: 'creative', view: 'series' },
  'capabilities/creative/production': { area: 'creative', view: 'production' },
  'capabilities/creative/direction': { area: 'creative', view: 'direction' },
  'capabilities/creative/commissions': { area: 'creative', view: 'commissions' },
  'capabilities/creative/exports': { area: 'creative', view: 'exports' },
  'capabilities/music/repertoire': { area: 'music', view: 'repertoire' },
  'capabilities/music/catalog': { area: 'music', view: 'catalog' },
  'capabilities/music/listening': { area: 'music', view: 'listening' },
  'capabilities/music/decks': { area: 'music', view: 'decks' },
  'capabilities/music/rounds': { area: 'music', view: 'rounds' },
  'capabilities/music/midi': { area: 'music', view: 'midi' },
  'capabilities/music/videos': { area: 'music', view: 'videos' },
  'capabilities/music/models3d': { area: 'music', view: 'models3d' },
  'capabilities/music/assemblies': { area: 'music', view: 'assemblies' },
  'capabilities/experience/stories': { area: 'experience', view: 'stories' },
  'capabilities/experience/games': { area: 'experience', view: 'games' },
  'capabilities/experience/world': { area: 'experience', view: 'world' },
  'capabilities/experience/foundations': { area: 'experience', view: 'foundations' },
  'capabilities/experience/travel': null,
  'capabilities/experience/voice': { area: 'experience', view: 'voice' },
  'capabilities/experience/calls': { area: 'experience', view: 'calls' },
  'capabilities/experience/moltworld': { area: 'experience', view: 'moltworld' },
  'capabilities/experience/moltbook': { area: 'experience', view: 'moltbook' },
})

type RecordBinding = Readonly<{ kind: string; endpoint: string; queryKey: string }>
const RECORDS: Readonly<Record<string, RecordBinding>> = Object.freeze({
  'capabilities/creative/ingredients': { kind: 'creative.ingredient', endpoint: '/api/capabilities/creative/ingredients', queryKey: 'ingredient' },
  'capabilities/creative/works': { kind: 'creative.work', endpoint: '/api/capabilities/creative/works', queryKey: 'work' },
  'capabilities/creative/series': { kind: 'creative.series', endpoint: '/api/capabilities/creative/series', queryKey: 'series' },
  'capabilities/media/sketches': { kind: 'media.sketch', endpoint: '/api/capabilities/media/sketches', queryKey: 'sketch' },
  'capabilities/media/library': { kind: 'media.artifact', endpoint: '/api/capabilities/media/library', queryKey: 'artifact' },
  'capabilities/media/timelines': { kind: 'media.timeline', endpoint: '/api/capabilities/media/timelines', queryKey: 'timeline' },
  'capabilities/media/jobs': { kind: 'media.job', endpoint: '/api/capabilities/media/jobs', queryKey: 'job' },
})

export type StudioRouteResolution = 'available' | 'missing' | 'denied' | 'unavailable'

export function studioDestination(route: ShellRoute) {
  if (route.destination !== 'apps' || route.view !== 'workspace') return null
  const id = route.placement?.id
  return id && Object.hasOwn(TARGETS, id) ? DESTINATIONS.find(entry => entry.id === id && entry.owner === 'studio') ?? null : null
}

export function studioTarget(route: ShellRoute): StudioTarget | null {
  const id = studioDestination(route)?.id
  if (!id) return null
  return TARGETS[id]
}

export function createStudioRoute(id: string, returnTo?: ShellReturnContext, ref?: StudioRecordRef, scope?: OwnerScope): ShellRoute {
  const entry = DESTINATIONS.find(item => item.id === id && item.owner === 'studio')
  if (!entry || !Object.hasOwn(TARGETS, id)) throw new TypeError('Unknown Studio destination')
  if (ref && (!scope || !sameStudioOwner(scope, ref) || RECORDS[id]?.kind !== ref.kind)) {
    throw new TypeError('This native record does not belong to the Studio destination or owner')
  }
  const details: Record<string, string> = {}
  if (ref?.revision !== undefined) details.revision = String(ref.revision)
  if (ref?.source?.conversationId) details.sourceConversation = ref.source.conversationId
  if (ref?.source?.runId) details.sourceRun = ref.source.runId
  if (ref?.providerCapability) details.providerCapability = ref.providerCapability
  if (ref?.job) { details.jobId = ref.job.id; details.jobStatus = ref.job.state }
  if (ref?.artifact) {
    details.artifactId = ref.artifact.id
    if (ref.artifact.version !== undefined) details.artifactVersion = String(ref.artifact.version)
    details.artifactAvailable = String(ref.artifact.available)
  }
  return createShellRoute('apps', { view: 'workspace', placement: { id, query: details },
    record: ref ? { kind: ref.kind, id: ref.id } : undefined, returnTo })
}

const JOB_STATES: ReadonlySet<string> = new Set<StudioJobState>(['queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'])

export function studioRouteRecord(route: ShellRoute, scope: OwnerScope): StudioRecordRef | null {
  const id = studioDestination(route)?.id
  if (!id || !route.record || RECORDS[id]?.kind !== route.record.kind) return null
  const query = route.placement?.query ?? {}
  const positive = (value: string | undefined): number | undefined => {
    if (value === undefined) return undefined
    if (!/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value))) throw new TypeError('Invalid Studio revision')
    return Number(value)
  }
  try {
    if (query.jobStatus && !JOB_STATES.has(query.jobStatus)) return null
    if (query.artifactAvailable && query.artifactAvailable !== 'true' && query.artifactAvailable !== 'false') return null
    if ((query.jobId && !query.jobStatus) || (!query.jobId && query.jobStatus)
      || (query.artifactId && !query.artifactAvailable) || (!query.artifactId && query.artifactAvailable)) return null
    return studioRecordRef(scope, {
      kind: route.record.kind, id: route.record.id,
      revision: positive(query.revision),
      source: query.sourceConversation || query.sourceRun
        ? { conversationId: query.sourceConversation, runId: query.sourceRun } : undefined,
      providerCapability: query.providerCapability,
      job: query.jobId ? { id: query.jobId, state: query.jobStatus as StudioJobState } : undefined,
      artifact: query.artifactId ? { id: query.artifactId,
        version: positive(query.artifactVersion), available: query.artifactAvailable === 'true' } : undefined,
    })
  } catch { return null }
}

export async function resolveStudioRoute(route: ShellRoute, scope: OwnerScope): Promise<StudioRouteResolution> {
  const entry = studioDestination(route)
  if (!entry || !scope.cacheKey || !scope.ownerId || !scope.runtimeOrigin
    || (typeof location !== 'undefined' && scope.runtimeOrigin !== location.origin)) return 'unavailable'
  if (entry.id !== 'capabilities/media/library') return 'unavailable'
  const binding = RECORDS[entry.id]
  if (route.record && (!binding || binding.kind !== route.record.kind)) return 'unavailable'
  try {
    const session = await readOwnerSession()
    if (session.user !== scope.ownerId) return 'denied'
    if (!route.record || !binding) return 'available'
    const native = await gatewayJson<unknown>(`${binding.endpoint}/${encodeURIComponent(route.record.id)}`)
    if (!native || typeof native !== 'object' || !('id' in native) || native.id !== route.record.id) return 'unavailable'
    const metadata = route.placement?.query
    if (metadata?.revision && Number(metadata.revision) !== (native as { revision?: unknown }).revision) return 'unavailable'
    if (metadata?.artifactVersion && Number(metadata.artifactVersion) !== (native as { version?: unknown }).version) return 'unavailable'
    if (metadata?.artifactId && metadata.artifactId !== route.record.id && entry.id === 'capabilities/media/library') return 'unavailable'
    return 'available'
  } catch (error) {
    if (error instanceof GatewayError) {
      if (error.status === 404) return 'missing'
      if (error.status === 401 || error.status === 403) return 'denied'
    }
    return 'unavailable'
  }
}
