export const ESCALATION_REASON_SENTENCES: Record<string, string> = {
  max_iterations: 'The loop reached its iteration limit.',
  repeated_error: 'The same error kept recurring.',
  identical_output: 'The loop kept producing identical output.',
  token_cap: 'The loop exhausted its token budget.',
  recoverable_exhausted: 'Transient failures continued beyond the recovery window.',
  environment_broken: 'The execution environment cannot support the next attempt.',
  identical_call: 'The same failing tool call kept recurring.',
  hypothesis_exhausted: 'Repeated fixes did not change the failure.',
  no_progress: 'The loop stopped making measurable progress.',
}

export function escalationReasonSentence(reason: unknown): string {
  const key = typeof reason === 'string' ? reason : ''
  return ESCALATION_REASON_SENTENCES[key] ?? 'The workflow could not recover automatically.'
}
