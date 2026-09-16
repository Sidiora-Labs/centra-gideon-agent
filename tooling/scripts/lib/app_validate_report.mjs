
export const LEGS = [
  { id: 'store-source', title: 'Register the bundle as a local Store source' },
  { id: 'store-card', title: 'Store card renders name, type, description, permissions, source kind' },
  { id: 'ui-install', title: 'Install through the UI, consent dialog included' },
  { id: 'library-and-tools', title: 'Lands in the Library and its tools render on the Tools page' },
  { id: 'tool-invoke', title: 'Invoke a tool from the UI and capture the result' },
  { id: 'reactivate', title: 'Deactivate then reactivate round-trip' },
]

export const STATUS = Object.freeze({
  PASS: 'PASS',
  FAIL: 'FAIL',
  SKIPPED: 'SKIPPED',
  PENDING: 'PENDING',
})

export const NOT_REACHED_REASON =
  'leg did not run — an earlier leg in this bundle did not complete'

export function newLeg({ id, title }) {
  if (!id) throw new Error('newLeg: id is required')
  return { id, title: title ?? id, status: STATUS.PENDING, reason: '', screenshots: [], notes: [], details: {} }
}

export function newLegs(legs = LEGS) {
  return legs.map(newLeg)
}

function findLeg(legs, id) {
  const leg = legs.find((l) => l.id === id)
  if (!leg) throw new Error(`unknown leg id: ${id}`)
  return leg
}

function applyEvidence(leg, { screenshots = [], notes = [], details = {} } = {}) {
  leg.screenshots = [...leg.screenshots, ...screenshots.filter(Boolean)]
  leg.notes = [...leg.notes, ...notes.filter(Boolean)]
  leg.details = { ...leg.details, ...details }
}

export function passLeg(legs, id, evidence) {
  const leg = findLeg(legs, id)
  leg.status = STATUS.PASS
  leg.reason = ''
  applyEvidence(leg, evidence)
  return leg
}

export function failLeg(legs, id, reason, evidence) {
  if (!reason || !String(reason).trim()) throw new Error(`failLeg(${id}): a reason is required`)
  const leg = findLeg(legs, id)
  leg.status = STATUS.FAIL
  leg.reason = String(reason).trim()
  applyEvidence(leg, evidence)
  return leg
}

export function skipLeg(legs, id, reason, evidence) {
  if (!reason || !String(reason).trim()) throw new Error(`skipLeg(${id}): a reason is required`)
  const leg = findLeg(legs, id)
  leg.status = STATUS.SKIPPED
  leg.reason = String(reason).trim()
  applyEvidence(leg, evidence)
  return leg
}

export function noteLeg(legs, id, evidence) {
  applyEvidence(findLeg(legs, id), evidence)
}

export function finalizeLegs(legs, reason = NOT_REACHED_REASON) {
  const why = String(reason || '').trim() || NOT_REACHED_REASON
  for (const leg of legs) {
    if (leg.status === STATUS.PENDING) {
      leg.status = STATUS.SKIPPED
      leg.reason = why
    }
  }
  return legs
}

export function bundleVerdict(legs) {
  if (!legs.length) return STATUS.FAIL
  if (legs.some((l) => l.status === STATUS.FAIL)) return STATUS.FAIL
  if (legs.every((l) => l.status === STATUS.PASS)) return STATUS.PASS
  return 'PARTIAL'
}

export function shapeBundleReport({ bundle, path, app = null, legs, consoleErrors = [], pageErrors = [], notReachedReason }) {
  const finalized = finalizeLegs(legs, notReachedReason)
  return {
    bundle,
    path,
    app,
    verdict: bundleVerdict(finalized),
    legs: finalized.map((l) => ({ ...l })),
    consoleErrors: [...consoleErrors],
    pageErrors: [...pageErrors],
  }
}

export function countStatuses(bundles) {
  const totals = { PASS: 0, FAIL: 0, SKIPPED: 0 }
  for (const b of bundles) for (const l of b.legs) totals[l.status] = (totals[l.status] ?? 0) + 1
  return totals
}

export function exitCodeFor(bundles) {
  return bundles.some((b) => b.verdict === STATUS.FAIL) ? 1 : 0
}

export const REPORT_SCHEMA = 'gideon.app-ui-validation/1'

export function shapeReport({ bundles, harness = {}, model = null, generatedAt }) {
  return {
    schema: REPORT_SCHEMA,
    generatedAt: generatedAt ?? new Date().toISOString(),
    harness,
    model,
    legs: LEGS.map((l) => ({ ...l })),
    bundles,
    totals: countStatuses(bundles),
    exitCode: exitCodeFor(bundles),
  }
}

export function formatReport(report) {
  const out = []
  for (const b of report.bundles) {
    out.push(`${b.bundle}: ${b.verdict}${b.app?.version ? ` (v${b.app.version})` : ''}`)
    for (const l of b.legs) {
      const why = l.reason ? ` — ${l.reason}` : ''
      out.push(`  ${l.status.padEnd(7)} ${l.id}${why}`)
      for (const s of l.screenshots) out.push(`          shot: ${s}`)
    }
  }
  const t = report.totals
  out.push(`totals: ${t.PASS} PASS · ${t.FAIL} FAIL · ${t.SKIPPED} SKIPPED`)
  return out.join('\n')
}
