import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const code = readFileSync(join(process.cwd(), 'src/features/loops/LoopCockpitPage.tsx'), 'utf8')

describe('loop cockpit accessibility', () => {
  it('reports changing loop status without interrupting the user', () => {
    expect(code).toMatch(/const statusLine = \(\s*<span role="status" aria-live="polite" aria-atomic="true"/)
  })

  it('announces banners with urgency that matches their impact', () => {
    expect(code).toMatch(/role=\{info \? 'status' : 'alert'\} aria-live=\{info \? 'polite' : 'assertive'\} aria-atomic="true"/)
    expect(code).toMatch(/\{judgeDegraded && running && \(\s*<div role="status" aria-live="polite" aria-atomic="true"/)
    expect(code).toMatch(/\{c\.status === 'needs_input' && c\.pending_question && \(\s*<div role="alert" aria-live="assertive" aria-atomic="true"/)
  })
})
