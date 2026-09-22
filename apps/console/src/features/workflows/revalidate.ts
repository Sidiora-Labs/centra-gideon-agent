import type { WorkflowCascadePreview } from '../../shared/data/api'

export const revalidateNotice =
  'Editing this stage changes the template. Its judge calibration was tuned to the ' +
  'shipped prompts, so re-validate the template after resuming — the judge may otherwise ' +
  'grade against a rubric this run no longer matches.'

export function revalidateSummary(preview: WorkflowCascadePreview | null | undefined): string {
  const rerun = preview?.rerun?.length ?? 0
  const head =
    rerun > 0
      ? `Edit applied — ${rerun} step${rerun === 1 ? '' : 's'} will re-run.`
      : 'Edit applied.'
  return `${head} Re-validate this template’s judge calibration.`
}

export function cascadeConfirmation(preview: WorkflowCascadePreview | null | undefined): string {
  const rerun = preview?.rerun?.length ?? 0
  const effects = preview?.committed_effects ?? []
  const rerunText = rerun === 1 ? '1 completed step will run again.' : `${rerun} completed steps will run again.`
  const effectText = effects.length === 0
    ? ''
    : ` Committed external effects: ${effects.join(', ')}. Re-running them may repeat an external action.`
  return `${rerunText}${effectText} Confirm this cascade to continue.`
}
