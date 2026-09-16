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
