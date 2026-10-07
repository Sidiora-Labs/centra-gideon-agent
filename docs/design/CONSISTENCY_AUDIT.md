# Design-system consistency audit

## Historical measurements

The earlier source scan recorded the following values. They are retained as a historical measurement, not a census of the current checkout. Console paths and primitive adoption have since changed.

| Historical metric | Recorded count |
| --- | --- |
| Source files scanned | 298 |
| Shared primitives counted | 33 |
| Raw-value drift hits | 7 |
| Raw button occurrences | 420 |
| Raw form elements | 206 |
| Files carrying bespoke controls | 112 |

These counts do not establish current accessibility, visual parity or CI status. Global focus and reduced-motion rules are useful safeguards, but their presence alone does not qualify every interactive surface.

## Current source of evidence

The reporter is [consistencyAudit.report.ts](../../apps/console/src/shared/theme/consistencyAudit.report.ts), with the opt-in writer in [consistencyAudit.generate.test.ts](../../apps/console/src/shared/theme/consistencyAudit.generate.test.ts). The root `npm run audit:consistency` command invokes the console workspace writer. Inspect its configured `AUDIT_JSON_PATH` and working directory before using the output: the destination is resolved from the process working directory, not from this Markdown file.

Static scans count source patterns; they do not execute controls or prove that a user can complete a task. Consult the current scanner and baseline files for exact exclusions and thresholds. Do not infer a fresh measurement from these historical tables.

For adoption guidance see [patterns](PATTERNS.md), [motion](MOTION.md) and [maintenance](CONSISTENCY_HANDOFF.md). Browser checks and their limits are documented in [the E2E guide](../../apps/console/e2e/README.md).
