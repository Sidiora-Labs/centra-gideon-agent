import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const CEILING = 669

const RAW_SIZE = /text-\[0?\.[0-9]+rem\]/g

const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

describe('raw arbitrary font sizes only ever decrease', () => {
  it(`stays at or under the ${CEILING} frozen on ratchet day`, () => {
    let count = 0
    for (const abs of walk(SRC)) {
      count += readFileSync(abs, 'utf8').match(RAW_SIZE)?.length ?? 0
    }
    expect(
      count,
      [
        `${count} raw arbitrary font sizes (ceiling ${CEILING}). New text sits on the type`,
        'scale via data-type roles from design/tokens.css, not text-[…rem]:',
        '  · 0.75rem   → data-type="caption"  (chip/badge/timestamp micro-text)',
        '  · 0.8125rem → data-type="body-s" for prose, data-type="label-s" for chip/meta',
        '  · 0.9375rem → data-type="body-m" / "label-m" / "title-m" by intent',
        'The role also carries line-height + weight — drop the leading-*/font-* utilities',
        'it makes redundant. If you migrated sites AWAY from raw sizes, lower CEILING to',
        'the new count in this same change.',
      ].join('\n'),
    ).toBeLessThanOrEqual(CEILING)
  })
})
