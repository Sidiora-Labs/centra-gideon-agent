import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const HERE = join(process.cwd(), "src/features/learning")

const PLURAL = /[A-Za-z]\((?:s|es)\)(?=[ ,·\n]|\)`)/

const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) sources(p, out)
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p)
  }
  return out
}

function offenders(): string[] {
  const out: string[] = []
  for (const abs of sources(HERE)) {
    const lines = stripComments(readFileSync(abs, 'utf8')).split('\n')
    lines.forEach((line, i) => {
      const m = PLURAL.exec(line)
      if (m && !/http\(s\)/.test(line)) {
        out.push(`${abs.slice(abs.indexOf('src/') + 4)}:${i + 1}  ${line.trim().slice(0, 80)}`)
      }
    })
  }
  return out
}

describe('#/learning writes real plurals, not "(s)"', () => {
  it('the scan reads the surface AND the pattern still matches one (double vacuity floor)', () => {
    expect(sources(HERE).length, 'no sources found under pages/learning').toBeGreaterThan(8)
    expect(PLURAL.test('{week.produced_total} proposal(s) filed'), 'the detector is broken').toBe(true)
    expect(PLURAL.test('over ${n} pass(es), and'), 'it must catch (es) too').toBe(true)
    expect(PLURAL.test('onClick={() => open(s)}'), 'a call is not a plural').toBe(false)
    expect(PLURAL.test('setBusy((s) => new Set(s).add(id))'), 'nor is Set(s).add').toBe(false)
  })

  it('no file on this surface ships a parenthetical plural', () => {
    expect(
      offenders(),
      'The count is in hand one token earlier at every one of these, so write the sentence: ' +
        '`${n} thing${n === 1 ? \'\' : \'s\'}` — the form 156 other sites in web/src already use.',
    ).toEqual([])
  })

  const CONVERTED: [string, number][] = [
    ['LearningPage.tsx', 3],
    ['HealthPanel.tsx', 6],
    ['learningMeta.ts', 2],
  ]

  it.each(CONVERTED)('%s still branches on all %d of its counts', (file, n) => {
    const src = readFileSync(join(HERE, file), 'utf8')
    const conditionals = [...src.matchAll(/=== 1 \? '' : '(s|es)'/g)]
    expect(
      conditionals.length,
      `${file}'s parentheticals were CONVERTED, not removed — dropping the count instead of ` +
        'pluralising it satisfies "no (s) here" while saying less than before',
    ).toBeGreaterThanOrEqual(n)
  })
})
