import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { execFileSync } from 'node:child_process'


const REPO = resolve(process.cwd(), '../..')

const read = (p: string) => readFileSync(join(REPO, p), 'utf8')

function capIn(source: string, marker: string): number | null {
  const at = source.indexOf(marker)
  if (at < 0) return null
  const m = /\.slice\(0,\s*(\d+)\)/.exec(source.slice(at, at + 400))
  return m ? Number(m[1]) : null
}

describe('both suggestion surfaces show the same amount of the same list', () => {
  const widget = read('apps/console/src/features/dashboard/widgets/Suggestions.tsx')
  const chat = read('apps/console/src/features/ChatPage.tsx')

  const dashCap = capIn(widget, 'items.slice')
  const chatCap = capIn(chat, 'api.suggestions()')
  const producer = JSON.parse(execFileSync(process.env.GIDEON_TEST_PYTHON || resolve(REPO, '.venv/bin/python'), ['-c', [
    'import ast,json,sys',
    'from pathlib import Path',
    'tree=ast.parse(Path(sys.argv[1]).read_text())',
    'wrapper=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="_parse_suggestions")',
    'call=wrapper.body[0].value',
    'assert isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=="parse"',
    'assert isinstance(call.func.value,ast.Call) and call.func.value.func.id=="_SuggestionResponse"',
    'response=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=="_SuggestionResponse")',
    'parser=next(node for node in response.body if isinstance(node,ast.FunctionDef) and node.name=="parse")',
    'caps=[node.value.slice.upper.value for node in ast.walk(parser) if isinstance(node,ast.Return) and isinstance(node.value,ast.Subscript) and isinstance(node.value.value,ast.Name) and node.value.value.id=="accepted"]',
    'assert len(caps)==1 and isinstance(caps[0],int)',
    'fallback=next(node.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=="_FALLBACK_SUGGESTIONS" for target in node.targets))',
    'print(json.dumps({"cap":caps[0],"fallbackCount":len(ast.literal_eval(fallback))}))',
  ].join('\n'), join(REPO, 'runtime/gideon/cognition/suggestions.py')], { encoding: 'utf8' })) as { cap: number; fallbackCount: number }
  const parserCap = producer.cap

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
    const n = producer.fallbackCount
    expect(n, 'the fallback list was not parsed — this assertion is vacuous').toBeGreaterThan(0)
    expect(
      dashCap,
      `the fallback list ships ${n} suggestions and #/dashboard renders ${dashCap}, so on every ` +
        `fresh install the last ${n - (dashCap ?? 0)} would be unreachable.`,
    ).toBeGreaterThanOrEqual(n)
  })
})
