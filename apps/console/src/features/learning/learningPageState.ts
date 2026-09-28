import { useMemo, useReducer, useRef } from 'react'
import { api, isFeatureOff, type LearningRow } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { FIELD_METRICS_KEY, fetchFieldMetrics } from './FieldMetricsPanel'
import { kindLabel } from './learningMeta'
import {
  HEALTH_KEY, IDENTITY_REPORT_KEY, JUDGE_BENCH_KEY, RETRIEVAL_BENCH_KEY,
  STUDIES_KEY, WEEK_KEY, proposalsKey, refreshAfterDecision, refreshEverything,
} from './proposalCache'

const ABLATION_KEY = 'learning:ablation'
const BENCHMARK_KEY = 'learning:benchmark'
const ATTENTION_KEY = 'learning:attention'

type ReviewState = { kind: string; error: string; pending: Set<string> }
type ReviewAction = { type: 'kind'; value: string } | { type: 'error'; value: string } | { type: 'pending'; id: string; active: boolean }
function reviewState(state: ReviewState, action: ReviewAction): ReviewState {
  if (action.type === 'kind') return { ...state, kind: action.value }
  if (action.type === 'error') return { ...state, error: action.value }
  const pending = new Set(state.pending)
  if (action.active) pending.add(action.id)
  else pending.delete(action.id)
  return { ...state, pending }
}

export function useLearningPage() {
  const [state, dispatch] = useReducer(reviewState, { kind: '', error: '', pending: new Set<string>() })
  const active = useRef(new Set<string>())
  const proposalsRead = useQuery(proposalsKey(state.kind), () => api.learningProposals(state.kind ? { kind: state.kind } : undefined))
  const weekRead = useQuery(WEEK_KEY, () => api.learningStagingWeek(7))
  const healthRead = useQuery(HEALTH_KEY, () => api.learningHealth(7))
  const attention = useQuery(ATTENTION_KEY, () => api.workflowAttention())
  const field = useQuery(FIELD_METRICS_KEY, fetchFieldMetrics)
  const judge = useQuery(JUDGE_BENCH_KEY, () => api.judgeBench())
  const studies = useQuery(STUDIES_KEY, () => api.evalStudies())
  const retrieval = useQuery(RETRIEVAL_BENCH_KEY, () => api.retrievalBench())
  const identityRead = useQuery(IDENTITY_REPORT_KEY, () => api.identityReport())
  const ablation = useQuery(ABLATION_KEY, () => api.ablation())
  const benchmark = useQuery(BENCHMARK_KEY, () => api.learningBenchmark())
  const learningOff = [proposalsRead.data, weekRead.data, healthRead.data, identityRead.data].some(isFeatureOff)
  const proposals = { ...proposalsRead, data: isFeatureOff(proposalsRead.data) ? undefined : proposalsRead.data }
  const week = { ...weekRead, data: isFeatureOff(weekRead.data) ? undefined : weekRead.data }
  const health = { ...healthRead, data: isFeatureOff(healthRead.data) ? undefined : healthRead.data }
  const identity = { ...identityRead, data: isFeatureOff(identityRead.data) ? undefined : identityRead.data }
  const facets = useMemo(() => {
    const inbox = proposals.data
    const items = [{ key: '', label: inbox ? `All (${inbox.total})` : 'All' }]
    for (const [key, count] of Object.entries(inbox?.by_kind ?? {})) {
      items.push({ key, label: `${kindLabel(key)} (${count})` })
    }
    return items
  }, [proposals.data])

  async function decide(row: LearningRow, verb: 'accept' | 'reject') {
    if (active.current.has(row.id)) return
    active.current.add(row.id)
    dispatch({ type: 'pending', id: row.id, active: true })
    dispatch({ type: 'error', value: '' })
    try {
      const operation = verb === 'accept' ? api.acceptLearningProposal : api.rejectLearningProposal
      await operation(row.id)
      refreshAfterDecision(proposals.refresh)
    } catch (error) {
      dispatch({ type: 'error', value: error instanceof Error ? error.message : `Could not ${verb} the proposal` })
    } finally {
      active.current.delete(row.id)
      dispatch({ type: 'pending', id: row.id, active: false })
    }
  }

  function refresh() {
    refreshEverything(proposals.refresh, week.refresh, health.refresh, judge.refresh, studies.refresh, retrieval.refresh, identity.refresh)
    for (const query of [ablation, benchmark, attention, field]) query.refresh()
  }

  return {
    state, learningOff, facets, proposals, week, health, attention, field, judge, studies, retrieval,
    identity, ablation, benchmark, decide, refresh,
    setKind: (value: string) => dispatch({ type: 'kind', value }),
    clearError: () => dispatch({ type: 'error', value: '' }),
  }
}
