import { invalidateKeys } from '../../shared/data/data'

export const PROPOSALS_KEY_PREFIX = 'learning:proposals:'
export const WEEK_KEY = 'learning:week'
export const HEALTH_KEY = 'learning:health'
export const JUDGE_BENCH_KEY = 'learning:judge-bench'
export const STUDIES_KEY = 'learning:studies'
export const RETRIEVAL_BENCH_KEY = 'learning:retrieval-bench'
export const IDENTITY_REPORT_KEY = 'learning:identity-report'
export const STUDY_DETAIL_KEY_PREFIX = 'learning:study:'

const invalidate = (scopes: readonly (readonly [string, boolean])[]) => {
  for (const [key, prefix] of scopes) invalidateKeys(key, prefix)
}
const proposalScope = [[PROPOSALS_KEY_PREFIX, true]] as const
const allScopes = [
  ...proposalScope, [WEEK_KEY, false], [HEALTH_KEY, false], [JUDGE_BENCH_KEY, false],
  [STUDIES_KEY, false], [STUDY_DETAIL_KEY_PREFIX, true], [RETRIEVAL_BENCH_KEY, false],
  [IDENTITY_REPORT_KEY, false],
] as const

export function studyDetailKey(studyId: string): string {
  return [STUDY_DETAIL_KEY_PREFIX, studyId].join('')
}
export function proposalsKey(kind: string): string {
  return [PROPOSALS_KEY_PREFIX, kind].join('')
}
export function refreshAfterDecision(refreshProposals: () => void): void {
  invalidate(proposalScope)
  refreshProposals()
}
export function refreshEverything(
  refreshProposals: () => void,
  refreshWeek: () => void,
  refreshHealth?: () => void,
  refreshJudgeBench?: () => void,
  refreshStudies?: () => void,
  refreshRetrieval?: () => void,
  refreshIdentity?: () => void,
): void {
  invalidate(allScopes)
  for (const refresh of [refreshProposals, refreshWeek, refreshHealth, refreshJudgeBench, refreshStudies, refreshRetrieval, refreshIdentity]) refresh?.()
}
