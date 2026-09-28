import { describe, expect, test } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { api, type FeatureOffEnvelope, type ReportNotRunEnvelope } from '../../shared/data/api'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { FieldMetricsPanel } from './FieldMetricsPanel'
import { StudiesPanel } from './StudiesPanel'

const off: FeatureOffEnvelope = { enabled: false }
const notRun: ReportNotRunEnvelope = { state: 'not_run', next_action: 'Run the benchmark to produce a report.' }

describe('readable report responses', () => {
  test('native reports render disabled and never-run envelopes before accessing report data', () => {
    for (const markup of [
      renderToStaticMarkup(<JudgeBenchPanel bench={off} error={undefined} onRetry={api.judgeBench} />),
      renderToStaticMarkup(<RetrievalBenchPanel bench={off} error={undefined} onRetry={api.retrievalBench} />),
      renderToStaticMarkup(<FieldMetricsPanel rows={off} error={undefined} onRetry={api.evalFieldMetrics} />),
      renderToStaticMarkup(<StudiesPanel studies={off} error={undefined} onRetry={api.evalStudies} />),
    ]) expect(markup).toContain('The eval substrate is off')

    for (const markup of [
      renderToStaticMarkup(<JudgeBenchPanel bench={notRun} error={undefined} onRetry={api.judgeBench} />),
      renderToStaticMarkup(<RetrievalBenchPanel bench={notRun} error={undefined} onRetry={api.retrievalBench} />),
    ]) {
      expect(markup).toContain('has run yet')
      expect(markup).toContain(notRun.next_action)
    }
  })
})
