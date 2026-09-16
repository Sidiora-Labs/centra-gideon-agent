import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const source = readFileSync(join(SRC, 'shared/ui/content/ContentSurface.tsx'), 'utf8')

function tagAt(from: number): string {
  let depth = 0
  for (let i = from; i < source.length; i++) {
    const ch = source[i]
    if (ch === '{') depth++
    else if (ch === '}') depth--
    else if (ch === '>' && depth === 0) return source.slice(from, i + 1)
  }
  return ''
}

function tagsFor(re: RegExp): string[] {
  return [...source.matchAll(re)].map((m) => tagAt(m.index!))
}

describe('the preview scrollers are keyboard-reachable', () => {
  const scrollers = tagsFor(/<div ref=\{(?:previewScrollRef|splitPreviewRef)\}/g)

  it('finds BOTH of them (not vacuously green)', () => {
    expect(scrollers.length, 'expected the preview and split preview scrollers').toBe(2)
  })

  for (const [i, tag] of scrollers.entries()) {
    it(`scroller ${i + 1} owns a tab stop, a role and a name`, () => {
      expect(tag, 'without a tab stop the preview cannot be scrolled by keyboard').toContain('tabIndex={0}')
      expect(tag, 'role=group keeps it announced as a container, not an unnamed widget').toContain('role="group"')
      expect(tag, 'an unnamed region announces nothing useful').toMatch(/aria-label=/)
    })
  }
})

describe('an in-flight save says it is busy', () => {
  const gated = tagsFor(/<button\b/g).filter((t) => /\bdisabled=\{[^}]*\bsaving\b/.test(t))

  it('finds the saving-gated buttons (not vacuously green)', () => {
    expect(gated.length, 'expected the save and action buttons').toBeGreaterThanOrEqual(2)
  })

  for (const [i, tag] of gated.entries()) {
    it(`saving-gated button ${i + 1} carries aria-busy`, () => {
      expect(
        tag,
        'a raw <button> disabled by `saving` shows only a decorative spinner — without\n' +
          'aria-busy the action announces nothing while it runs:\n  ' + tag.replace(/\s+/g, ' ').slice(0, 120),
      ).toMatch(/aria-busy=\{saving \|\| undefined\}/)
    })
  }

  it('does not emit aria-busy="false" when idle', () => {
    for (const tag of gated) expect(tag).not.toMatch(/aria-busy=\{saving\}/)
  })
})
