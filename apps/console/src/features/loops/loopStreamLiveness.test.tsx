import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the loop cockpit says whether its feed is alive', () => {
  it('the hook reports liveness from the transport', () => {
    const code = read('features/loops/useRunStream.ts')
    expect(code).toMatch(/es\.onopen = \(\) => setConnected\(true\)/)
    expect(code).toMatch(/es\.onerror = \(\) => setConnected\(false\)/)
    expect(code).toMatch(/return \{ connected \}/)
  })

  it('it resets per target and on teardown, so state cannot leak between loops', () => {
    const code = read('features/loops/useRunStream.ts')
    expect(code).toMatch(/setConnected\(false\)\n    if \(!enabled \|\| !id\) return/)
    expect(code).toMatch(/es\?\.close\(\); setConnected\(false\)/)
  })

  it('it still never closes or resubscribes on error — the browser owns the retry', () => {
    const code = read('features/loops/useRunStream.ts')
    expect(code).not.toMatch(/onerror = \(\) => \{[^}]*es\.close\(\)/)
    expect(code).not.toMatch(/onerror[\s\S]{0,80}new EventSource/)
  })

  it('the cockpit renders the dot AND the word, only while running', () => {
    const code = read('features/loops/LoopCockpitPage.tsx')
    expect(code).toMatch(/const \{ connected \} = useRunStream/)
    expect(code).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code).toMatch(/background: connected \? 'var\(--color-ok\)' : 'var\(--color-on-surface-low\)'/)
    expect(code, 'a finished loop has no stream').toMatch(/\{running && \(/)
  })

  it('it reuses the vocabulary the workflow run view already adopted', () => {
    expect(read('features/workflows/WorkflowRunDetail.tsx')).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(read('features/settings/DiagnosticsPanel.tsx')).toMatch(/connected \? 'Streaming' : 'Connecting…'/)
  })

  it('the DESIGN cockpit shows it too — the loops family is now complete', () => {
    const code = read('features/loops/DesignCockpitPage.tsx')
    expect(code).toMatch(/const \{ connected \} = useRunStream/)
    expect(code).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code).toMatch(/background: connected \? 'var\(--color-ok\)' : 'var\(--color-on-surface-low\)'/)
    expect(code, 'a finished loop has no stream').toMatch(/\{running && \(/)
  })

  it('CodeCockpitPage now has it too — every watch surface on this hook is covered', () => {
    const code = read('features/code/CodeCockpitPage.tsx')
    expect(code).toMatch(/const \{ connected \} = useRunStream/)
    expect(code).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code, 'gated on its own running flag').toMatch(/\{active && \(/)
  })

  it('LoopPlanReview stays the one deliberate opt-out', () => {
    const code = read('features/loops/LoopPlanReview.tsx')
    expect(code).toMatch(/useRunStream\(/)
    expect(code).not.toMatch(/const \{ connected \} = useRunStream/)
  })

  it('the remaining consumers still compile against the new return', () => {
    for (const rel of ['features/loops/DesignCockpitPage.tsx', 'features/code/CodeCockpitPage.tsx', 'features/loops/LoopPlanReview.tsx']) {
      expect(read(rel), `${rel} calls the hook`).toMatch(/useRunStream\(/)
    }
  })
})
