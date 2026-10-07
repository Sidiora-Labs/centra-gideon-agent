import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('an agent row makes its full name and description recoverable', () => {
  const PAGE = strip(read('features/agents/AgentsListPage.tsx'))
  const CARD = strip(read('shared/vendor/assistant-ui/elements/agent-card.tsx'))
  const row = (name: string) => {
    const source = PAGE.match(new RegExp(`function ${name}\\([\\s\\S]*?(?=\\nfunction |$)`))?.[0] ?? ''
    expect(source, `found ${name}`).not.toBe('')
    return source
  }

  it('the native row delegates its actual name and description to the real card', () => {
    expect(PAGE).toContain("import { AgentCard } from '../../shared/vendor/assistant-ui/elements/agent-card'")
    expect(row('NativeRow')).toContain("<AgentCard name={agent.name} description={agent.description ?? ''}")
    expect(CARD).toContain('export function AgentCard(')
  })

  it('the clipped native card name carries the complete name as its title', () => {
    const name = CARD.match(/<span[^>]*className="truncate[^>]*>\{name\}<\/span>/)?.[0] ?? ''
    expect(name, 'the delegated name element must exist').not.toBe('')
    expect(name).toContain('title={name}')
  })

  it('the native description remains completely visible instead of being clipped', () => {
    const description = CARD.match(/\{description && <p([^>]*)>[\s\S]*?\{description\}[\s\S]*?<\/p>/)
    expect(description, 'the actual description paragraph must exist').not.toBeNull()
    expect(description![1]).toContain('leading-relaxed')
    expect(description![1]).not.toMatch(/truncate|line-clamp|overflow-hidden/)
  })

  it('the discovered row recovers both clipped fields with their unmodified titles', () => {
    const discovered = row('DiscoveredRow')
    expect(discovered).toMatch(/<span[^>]*className="block truncate[^>]*title=\{agent\.name\}>\{agent\.name\}<\/span>/)
    expect(discovered).toMatch(/<p[^>]*className="mt-0\.5 truncate[^>]*title=\{agent\.description\}>\{agent\.description\}<\/p>/)
    expect((discovered.match(/title=\{agent\.name\}/g) || []).length).toBe(1)
    expect((discovered.match(/title=\{agent\.description\}/g) || []).length).toBe(1)
  })

  it('neither model nor separator is added to the full description', () => {
    const native = row('NativeRow')
    expect(native).toContain("description={agent.description ?? ''}")
    expect(native).toContain('model={agent.model}')
    expect(native).not.toMatch(/description=\{[^}]*agent\.model/)
    expect(row('DiscoveredRow')).toContain('title={agent.description}>{agent.description}')
  })

  it('both native and discovered rows keep their actual accessible names and opening controls', () => {
    const native = row('NativeRow')
    expect(native).toContain('role="button" tabIndex={0} aria-label={agent.name}')
    expect(native).toContain('onClick={onClick}')
    expect(native).toContain("event.key === 'Enter' || event.key === ' '")
    expect(row('DiscoveredRow')).toMatch(/<ListRow[^>]*onClick=\{onClick\} label=\{agent\.name\}/)
  })
})
