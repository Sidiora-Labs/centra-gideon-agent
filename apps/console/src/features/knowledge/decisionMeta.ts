import type { CalibrationBucket, DecisionJournalView, DecisionRow } from '../../shared/data/api'

export type CalibrationState = 'calibrated' | 'too-few' | 'no-data'

export function calibrationState(buckets: Record<string, CalibrationBucket>): CalibrationState {
  const rows = Object.values(buckets || {})
  if (rows.length === 0) return 'no-data'
  return rows.some((b) => b.count_honest) ? 'calibrated' : 'too-few'
}

export function calibrationCaption(view: Pick<DecisionJournalView, 'calibration' | 'calibration_min_n' | 'decisions'>): string {
  const state = calibrationState(view.calibration)
  const minN = view.calibration_min_n
  if (state === 'calibrated') {
    const honest = Object.values(view.calibration).filter((b) => b.count_honest).length
    return `Calibration across ${honest} domain${honest === 1 ? '' : 's'} with at least ${minN} resolved decisions.`
  }
  if (state === 'too-few') {
    const n = Object.values(view.calibration).reduce((t, b) => t + b.n, 0)
    return `${n} resolved decision${n === 1 ? '' : 's'} — too few to mean much. No domain has reached ${minN} yet, so no rate is shown.`
  }
  const pending = (view.decisions || []).filter((d) => d.status === 'pending').length
  if (pending > 0) {
    return `${pending} decision${pending === 1 ? '' : 's'} still open and none resolved yet — calibration starts once outcomes come in.`
  }
  return 'No decisions logged yet. Calibration appears once you have logged some and recorded how they turned out.'
}

export function bucketLabel(b: CalibrationBucket, minN: number): string {
  if (!b.count_honest) {
    return `${b.n} of ${minN} decisions — too few to mean much`
  }
  const rate = Math.round((b.as_expected_rate ?? 0) * 100)
  const conf = b.mean_confidence == null ? 'no stated confidence' : `${Math.round(b.mean_confidence * 100)}% mean confidence`
  return `${b.n} decisions · ${rate}% resolved as expected · ${conf}`
}

export function bucketPlottable(b: CalibrationBucket): boolean {
  return b.count_honest && b.as_expected_rate != null
}

export type PendingState = 'stale' | 'overdue' | 'counting'

export function pendingState(d: DecisionRow): PendingState {
  if (d.stale_pending) return 'stale'
  if (d.overdue) return 'overdue'
  return 'counting'
}

export function horizonLabel(d: DecisionRow, now: Date = new Date()): string {
  const at = Date.parse(d.review_horizon || '')
  if (!Number.isFinite(at)) return ''
  const days = Math.round((at - now.getTime()) / 86400000)
  const state = pendingState(d)
  const ago = `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`
  const times = `deferred ${d.deferrals} time${d.deferrals === 1 ? '' : 's'}`
  if (state === 'stale') {
    return days < 0
      ? `Review lapsed ${ago} ago · ${times}, no reminder left`
      : `${times[0].toUpperCase()}${times.slice(1)} — no reminder left, so nothing will bring this back`
  }
  if (state === 'overdue') return `Review was due ${ago} ago`
  if (days === 0) return 'Review due today'
  return `Review in ${ago}`
}

const GRADE_LABEL: Record<string, string> = {
  better: 'better than expected',
  as_expected: 'as expected',
  worse: 'worse than expected',
  mixed: 'mixed',
  too_early: 'too early to tell',
}
export function gradeLabel(grade: string | null): string {
  return GRADE_LABEL[grade || ''] ?? 'ungraded'
}

export function confidenceLabel(d: DecisionRow): string {
  return d.confidence == null ? 'no stated confidence' : `${Math.round(d.confidence * 100)}% confident`
}
