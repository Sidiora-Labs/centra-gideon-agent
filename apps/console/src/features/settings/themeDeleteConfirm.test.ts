import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/settings/DesignPanel.tsx"), 'utf8')
const CODE = SRC.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('a saved theme is not deleted on one click', () => {
  it('the tile delete goes through the confirm, not straight to the API', () => {
    expect(CODE, 'the tile calls the guarded helper').toMatch(/onDelete=\{isCustom\(s\.id\) \? \(\) => removeScheme\(s\) : undefined\}/)
    expect(CODE, 'and nothing calls the delete unguarded any more')
      .not.toMatch(/onDelete=\{isCustom\(s\.id\) \? \(\) => deleteCustomScheme/)
  })

  it('the confirm precedes the delete, and a cancel stops it', () => {
    expect(CODE).toMatch(/const ok = await confirmDelete\('theme', s\.label/)
    expect(CODE, 'an unconfirmed delete returns early').toMatch(/if \(!ok\) return\s*\n\s*await deleteCustomScheme\(s\.id\)/)
  })

  it('it uses the app-wide helper rather than a bespoke dialog', () => {
    expect(SRC).toMatch(/import \{ confirmDelete \} from '\.\.\/\.\.\/shared\/ui\/dialog'/)
  })

  it('the body tells the truth about the in-use case, and only then', () => {
    expect(CODE).toMatch(/const inUse = activeScheme === s\.id/)
    expect(CODE).toMatch(/inUse\s*\n?\s*\? 'You are using this theme, so the app goes back to its default colors/)
    expect(CODE, 'and the not-in-use branch says less').toMatch(/: 'It cannot be undone — a saved theme is a file, not a snapshot\.'/)
  })

  it('and spells it the American way, like the rest of the shipped copy', () => {
    const bodies = CODE.match(/'[^']*cannot be undone[^']*'/g) || []
    expect(bodies.length, 'both dialog bodies found').toBe(2)
    for (const b of bodies) expect(b, `${b} uses the repo's spelling`).not.toMatch(/colour/i)
  })

  it('only a CUSTOM tile offers deletion at all — the vacuity floor', () => {
    expect(CODE).toMatch(/isCustom\(s\.id\) \? \(\) => removeScheme\(s\) : undefined/)
  })

  it('the control is still the same hover-revealed trash, not a new affordance', () => {
    expect(CODE).toMatch(/title="Delete saved theme"/)
  })
})
