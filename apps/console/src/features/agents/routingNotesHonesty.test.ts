import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const src = readFileSync(join(__dirname, 'AgentDetail.tsx'), 'utf-8')

describe('routing-notes copy', () => {
  it('separates delegation notes from automatic chat suggestions', () => {
    expect(src).toContain('A note the default agent reads when choosing a specialist to delegate to.')
    expect(src).toContain('Use Specialty to control automatic chat suggestions.')
    expect(src).not.toContain('note the auto-router reads to pick between agents')
  })
})
