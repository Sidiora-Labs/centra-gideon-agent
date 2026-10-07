import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(import.meta.dirname, "../..")
const stripComments = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => stripComments(readFileSync(join(SRC, rel), 'utf8'))

describe('the native agent default marker signals the active agent, not the native group', () => {
  const src = read('features/agents/AgentsListPage.tsx')

  const nativeStart = src.indexOf('function NativeRow(')
  const nativeEnd = src.indexOf('function DiscoveredRow(')
  const nativeRow = src.slice(nativeStart, nativeEnd)

  it('reads the real NativeRow (not vacuously green)', () => {
    expect(nativeStart, 'NativeRow moved — this rail measures nothing').toBeGreaterThan(-1)
    expect(nativeEnd, 'DiscoveredRow moved — the slice is unbounded').toBeGreaterThan(nativeStart)
    expect(nativeRow, 'NativeRow must still render the real AgentCard for default context').toMatch(/<AgentCard\b/)
    expect(nativeRow, 'NativeRow must still know which agent is the default').toMatch(/\bisDefault\b/)
  })

  it('gates the native default marker on isDefault', () => {
    expect(nativeRow, 'the default marker must ride isDefault, so it means active rather than native').toMatch(
      /isDefault && <span[^>]*className="[^"]*text-primary[^"]*"[^>]*><Star[^>]*\/> default<\/span>/,
    )
  })

  it('no longer paints the rail on every native row unconditionally', () => {
    expect(nativeRow, 'the unconditional coral rail is the One Voice violation this fix removes').not.toMatch(
      /accent="var\(--color-primary\)"/,
    )
  })

  it('leaves the runtime (discovered) rows with no rail', () => {
    const discovered = src.slice(nativeEnd)
    expect(discovered, 'DiscoveredRow must exist for this assertion to mean anything').toMatch(/<ListRow\b/)
    expect(discovered, 'runtime agents are read-only and carry no coral accent').not.toMatch(/accent=/)
  })
})
