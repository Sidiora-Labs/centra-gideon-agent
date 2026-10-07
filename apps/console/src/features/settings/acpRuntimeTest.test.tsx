import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(join(process.cwd(), 'src/features/settings/AgentDefaultsPanel.tsx'), 'utf8')

describe('ACP runtime Test action wiring', () => {
  it('calls the explicit provider test endpoint and waits to display its result', () => {
    expect(source).toContain('api.testModelProvider(row.runtime_id)')
    expect(source).toContain('setTestMessage(result.message')
    expect(source).toContain('setTesting(false)')
  })

  it('does not allow a custom runner test before its owner grant is allowed', () => {
    expect(source).toContain("ownerGrant?.allowed === false")
    expect(source).toContain("disabledReason={row.source === 'user'")
  })
})
