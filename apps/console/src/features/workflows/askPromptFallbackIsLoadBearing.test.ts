import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/workflows/WorkflowAsk.tsx")
const raw = readFileSync(SRC, 'utf8')

describe('the gate panel still answers an empty prompt', () => {
  it('renders a sentence rather than an empty paragraph', () => {
    expect(raw, 'the prompt slot must keep its fallback').toContain(
      "{ask.prompt || 'This run needs your input.'}",
    )
  })

  it('the fallback is a real sentence, not a placeholder', () => {
    const m = /ask\.prompt \|\| '([^']+)'/.exec(raw)
    const fallback = m?.[1] ?? ''
    expect(fallback.length, 'a blank or stub fallback is the defect this guards').toBeGreaterThan(10)
    expect(fallback).toMatch(/[.!?]$/)
    expect(fallback).not.toBe('Approval needed')
  })
})
