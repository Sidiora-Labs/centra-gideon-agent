import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const src = readFileSync(join(__dirname, "AgentsListPage.tsx"), 'utf-8')

describe('the Native section caption is honest about built-ins', () => {
  it('names both populations and their real capabilities', () => {
    expect(src).toContain(
      'subtitle="Built-ins run the platform — definition fixed, model swappable. Agents you create are fully editable."',
    )
  })

  it('no blanket editability claim rides over a group that renders reserved rows', () => {
    expect(src, 'this page still renders reserved built-ins in the group').toContain('isReservedAgent(agent)')
    const captions = [...src.matchAll(/subtitle="([^"]*)"/g)].map((m) => m[1])
    expect(captions.length, 'the section captions this scan is protecting').toBeGreaterThan(0)
    for (const c of captions) {
      expect(c, `caption over-promises: "${c}"`).not.toMatch(/definitions\s*—\s*fully editable/i)
    }
  })
})
