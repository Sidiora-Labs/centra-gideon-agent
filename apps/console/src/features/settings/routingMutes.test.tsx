import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { canonicalAgentKey } from '../../shared/data/agents'

const source = (path: string) => readFileSync(join(process.cwd(), 'src', path), 'utf8')

describe('routing mutes', () => {
  it('uses canonical identity and one shared invalidated cache', () => {
    expect(canonicalAgentKey(' DBA ')).toBe(canonicalAgentKey('dba'))
    const shared = source('shared/data/agents.ts')
    expect(shared).toContain("'agents:routing-mutes'")
    expect(shared).toMatch(/routingUnmute[\s\S]*invalidateKeys\(AGENT_ROUTING_MUTES_KEY\)/)
    expect(source('features/agents/AgentDetail.tsx')).toContain('AGENT_ROUTING_MUTES_KEY')
    expect(source('features/settings/ChatPanel.tsx')).toContain('AGENT_ROUTING_MUTES_KEY')
  })

  it('always exposes the named unmute control and the third-dismissal announcement', () => {
    const settings = source('features/settings/ChatPanel.tsx')
    expect(settings).toMatch(/<Row label="Muted agents"/)
    expect(settings).toContain('ariaLabel={`Unmute ${agent}`}')
    expect(source('features/chat/RoutingChip.tsx')).toContain('Undo this in Settings › Chat › Agent routing.')
  })
})
