import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const widgets = readFileSync(join(SRC, 'features/settings/settingsWidgets.tsx'), 'utf8')
const code = widgets.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const CAN_FAIL: [string, string][] = [
  ['Inbox', 'inboxErr'],
  ['Apps', 'appsErr'],
  ['Archive', 'archErr'],
  ['Tool output', 'rulesErr'],
  ['Agent defaults', 'agentErr'],
]

describe('a hub tile whose data can fail says so instead of shimmering', () => {
  for (const [title, alias] of CAN_FAIL) {
    it(`the ${title} tile does not treat a failure as loading`, () => {
      const at = code.indexOf(`title="${title}"`)
      expect(at, `the ${title} tile must still exist`).toBeGreaterThan(-1)
      const tag = code.slice(at, at + 400)
      expect(tag, 'the shimmer must yield to the error').toMatch(
        new RegExp(`loading=\\{\\w+ === undefined && !${alias}\\}`),
      )
    })

    it(`the ${title} tile renders a failure line`, () => {
      const at = code.indexOf(`title="${title}"`)
      const body = code.slice(at, at + 1200)
      expect(body).toMatch(new RegExp(`Boolean\\(${alias}\\)`))
      expect(body, 'and it names what failed').toMatch(/Couldn&rsquo;t load/)
    })

    it(`the ${title} tile reads the error off its hook`, () => {
      expect(code).toMatch(new RegExp(`error: ${alias}`))
    })
  }

  it('the tool-output failure line yields to its live savings meter', () => {
    const at = code.indexOf('title="Tool output"')
    expect(code.slice(at, at + 900)).toMatch(/Boolean\(rulesErr\) && savedTokens === 0/)
  })

  it('the 22 hooks that still substitute a value are NOT given a failure line', () => {
    const starts = [...code.matchAll(/const (use\w+) = \(\) =>/g)]
    const swallowing = starts
      .filter((m, i) => /\.catch\(\(\)\s*=>/.test(code.slice(m.index!, starts[i + 1]?.index ?? m.index! + 700)))
      .map((m) => m[1])
    expect(swallowing.length, 'if this count moves, re-measure which tiles can fail')
      .toBeGreaterThanOrEqual(18)
    for (const alias of ['inboxErr', 'appsErr', 'archErr', 'rulesErr']) {
      expect(swallowing, `${alias}'s hook must NOT be among the swallowers`).not.toContain(
        { inboxErr: 'useInbox', appsErr: 'useApps', archErr: 'useArchives', rulesErr: 'useProjectionRules' }[alias],
      )
    }
  })

  it('no new visual idiom was invented — the line is the tile\'s own muted type', () => {
    const lines = [...code.matchAll(/Couldn&rsquo;t load[^<]*<\/div>/g)]
    expect(lines.length, 'five tiles, five lines').toBeGreaterThanOrEqual(CAN_FAIL.length)
    const styled = [...code.matchAll(/data-type="caption" className="text-on-surface-low">Couldn&rsquo;t load/g)]
    expect(styled.length, 'every one of them uses the same muted type as the original').toBeGreaterThanOrEqual(CAN_FAIL.length)
  })
})
