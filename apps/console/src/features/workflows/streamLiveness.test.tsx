import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the workflow run view says whether its feed is alive', () => {
  it('the hook reports liveness from the transport, not from a guess', () => {
    const code = read('features/workflows/useWorkflowStream.ts')
    expect(code).toMatch(/es\.onopen = \(\) => setConnected\(true\)/)
    expect(code).toMatch(/es\.onerror = \(\) => setConnected\(false\)/)
    expect(code, 'and hands it to the caller').toMatch(/return \{ connected \}/)
  })

  it('it resets when the target run changes, so state cannot leak between runs', () => {
    const code = read('features/workflows/useWorkflowStream.ts')
    expect(code).toMatch(/useEffect\(\(\) => \{\s*\n\s*setConnected\(false\)/)
    expect(code, 'and on teardown').toMatch(/es\?\.close\(\); setConnected\(false\)/)
  })

  it('it still never closes or resubscribes on error — the browser owns the retry', () => {
    const code = read('features/workflows/useWorkflowStream.ts')
    expect(code).not.toMatch(/onerror = \(\) => \{[^}]*es\.close\(\)/)
    expect(code).not.toMatch(/onerror[\s\S]{0,80}new EventSource/)
  })

  it('the run view renders the dot AND the word', () => {
    const code = read('features/workflows/WorkflowRunDetail.tsx')
    expect(code).toMatch(/const \{ connected \} = useWorkflowStream/)
    expect(code, 'the word carries the state').toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code, 'the colour only confirms it')
      .toMatch(/background: connected \? 'var\(--color-ok\)' : 'var\(--color-on-surface-low\)'/)
  })

  it('it shows only while the run is live', () => {
    expect(read('features/workflows/WorkflowRunDetail.tsx')).toMatch(/\{live && \(\s*\n\s*<span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low/)
  })

  it('the form it converges on is still the one DiagnosticsPanel ships', () => {
    const diag = read('features/settings/DiagnosticsPanel.tsx')
    expect(diag).toMatch(/connected \? 'Streaming' : 'Connecting…'/)
    expect(diag).toMatch(/size-1\.5 rounded-pill/)
  })

  it('the chat card deliberately opts out', () => {
    const card = read('features/chat/WorkflowProgressCard.tsx')
    expect(card, 'it calls the hook').toMatch(/useWorkflowStream\(/)
    expect(card, 'and ignores the liveness return by design — a chat turn is not a watch surface')
      .not.toMatch(/\{ connected \} = useWorkflowStream/)
  })
})
