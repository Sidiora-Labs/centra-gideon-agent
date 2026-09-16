import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { lessonPreview } from './MemoryPanel'
import type { Lesson } from '../../shared/data/api'


const base: Lesson = { rule: 'prefer ruff over flake8', category: 'tool' }

describe('a lesson row says whether it is in prompts', () => {
  it('names the injected state in words, with its confidence', () => {
    const line = lessonPreview({ ...base, standing: 'injected', confidence: 0.65 })
    expect(line).toContain('in prompts')
    expect(line).toContain('65%')
    expect(line).toContain('tool')
  })

  it('names the held state in words rather than by absence', () => {
    const line = lessonPreview({ ...base, standing: 'retained', confidence: 0.29 })
    expect(line).toContain('held below the gate')
    expect(line).toContain('29%')
    expect(line).not.toEqual(lessonPreview({ ...base, standing: 'injected', confidence: 0.29 }))
  })

  it('claims no standing when the server reported none', () => {
    expect(lessonPreview(base)).toEqual('tool')
    expect(lessonPreview({ ...base, category: '' })).toEqual('lesson')
  })

  it('rounds a missing confidence to 0% rather than rendering NaN', () => {
    expect(lessonPreview({ ...base, standing: 'retained' })).toContain('0% confidence')
  })
})

describe('the inspector shows the evidence, not a second opinion', () => {
  const src = readFileSync(join(__dirname, "MemoryPanel.tsx"), 'utf8')

  it('renders the standing, the confidence and the server-composed reason', () => {
    expect(src).toContain("['In prompts'")
    expect(src).toContain("['Confidence'")
    expect(src).toContain('confidence_reason')
    expect(src).toContain("['Observed'")
  })

  it('never recomposes the reason sentence in the frontend', () => {
    expect(src).not.toMatch(/observed \$\{[^}]*observations/)
    expect(src).not.toMatch(/below the \$\{[^}]*(threshold|gate)/)
  })
})
