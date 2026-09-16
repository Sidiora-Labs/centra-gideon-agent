import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const REPO = join(import.meta.dirname, "../../../../..")

const read = (p: string) => readFileSync(join(REPO, p), 'utf8')

function capIn(source: string, marker: string): number | null {
  const at = source.indexOf(marker)
  if (at < 0) return null
  const m = /\.slice\(0,\s*(\d+)\)/.exec(source.slice(at, at + 400))
  return m ? Number(m[1]) : null
}

describe('both suggestion surfaces show the same amount of the same list', () => {
  const widget = read('web/src/pages/dashboard/widgets/Suggestions.tsx')
  const chat = read('web/src/pages/ChatPage.tsx')
  const py = read('runtime/gideon/cognition/suggestions.py')

  const dashCap = capIn(widget, 'items.slice')
  const chatCap = capIn(chat, 'api.suggestions()')
  const parserCap = (() => {
    const at = py.indexOf('def _parse_suggestions')
    if (at < 0) return null
    const body = py.slice(at, py.indexOf('\ndef ', at + 1))
    const m = /\]\[:(\d+)\]/.exec(body)
    return m ? Number(m[1]) : null
  })()

  it('the three numbers were all found (vacuity floor)', () => {
    expect(dashCap, "the dashboard widget's slice(0, N) was not found").not.toBeNull()
    expect(chatCap, "ChatPage's SuggestionChips slice(0, N) was not found").not.toBeNull()
    expect(parserCap, "_parse_suggestions' [:N] cap was not found in suggestions.py").not.toBeNull()
  })

  it('the dashboard and the chat hero agree', () => {
    expect(
      dashCap,
      `#/dashboard shows ${dashCap} suggestions and #/chat shows ${chatCap} from the same ` +
        `endpoint. One of the two surfaces is lying about how many you have.`,
    ).toBe(chatCap)
  })

  it('neither consumer caps below the producer, which would discard generated work', () => {
    for (const [name, cap] of [
      ['#/dashboard', dashCap],
      ['#/chat', chatCap],
    ] as const) {
      expect(
        cap,
        `${name} shows ${cap} of the up-to-${parserCap} suggestions the backend produces. ` +
          `Every one is generated per user from their own memory, so a consumer cap below the ` +
          `producer's throws that away with no way to reach it. Raise the consumer, or lower the ` +
          `producer in suggestions.py so nothing is generated that cannot be seen.`,
      ).toBeGreaterThanOrEqual(parserCap!)
    }
  })

  it("the shipped fallback list fits — it is what a brand-new install always sees", () => {
    const block = py.slice(py.indexOf('_FALLBACK_SUGGESTIONS = ['))
    const list = block.slice(0, block.indexOf(']'))
    const n = (list.match(/^\s*"/gm) || []).length
    expect(n, 'the fallback list was not parsed — this assertion is vacuous').toBeGreaterThan(0)
    expect(
      dashCap,
      `the fallback list ships ${n} suggestions and #/dashboard renders ${dashCap}, so on every ` +
        `fresh install the last ${n - (dashCap ?? 0)} would be unreachable.`,
    ).toBeGreaterThanOrEqual(n)
  })
})
