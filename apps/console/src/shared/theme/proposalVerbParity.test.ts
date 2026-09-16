import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")

function pageFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) out.push(p)
    }
  }
  walk(PAGES)
  return out
}

const LABELS = /<Button\b[^>]*>([\s\S]{0,200}?)<\/Button>/g
const ACCEPTISH = /\bAccept\b|\bInstall skill\b/
const DECLINE = /\b(Reject|Dismiss|Decline|Discard)\b/

describe('accept/decline verb parity', () => {
  const files = pageFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.endsWith(join('learning', 'LearningPage.tsx')))).toBe(true)
  })

  it('the decline half of an accept/decline pair says "Reject"', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      const buttons = [...src.matchAll(LABELS)].map((m) => ({ text: m[1], at: m.index ?? 0 }))
      const accepts = buttons.filter((b) => ACCEPTISH.test(b.text))
      if (!accepts.length) continue
      for (const a of accepts) {
        const near = buttons.filter((b) => Math.abs(b.at - a.at) < 900 && DECLINE.test(b.text))
        for (const n of near) {
          const word = (n.text.match(DECLINE) ?? [])[1]
          if (word && word !== 'Reject') {
            const line = src.slice(0, n.at).split('\n').length
            offenders.push(`${f.slice(PAGES.length + 1)}:${line} — "${word}" paired with Accept`)
          }
        }
      }
    }
    expect(
      [...new Set(offenders)],
      'The negative half of an Accept/Decline pair must say "Reject". "Dismiss" means something ' +
        'else in this app — triaging an item off a list (InboxDetail writes status: dismissed) — ' +
        'so using it to decline a proposal promises the wrong outcome:\n  ' +
        [...new Set(offenders)].join('\n  '),
    ).toEqual([])
  })

  it('"Dismiss" is still available for its own meaning', () => {
    const inbox = readFileSync(join(PAGES, 'inbox/InboxDetail.tsx'), 'utf8')
    expect(inbox).toMatch(/Dismiss/)
    expect(inbox, 'InboxDetail should still write the dismissed status').toMatch(/status:\s*'dismissed'/)
  })
})
