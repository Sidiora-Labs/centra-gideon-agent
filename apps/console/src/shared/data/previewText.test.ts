import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { previewText } from './previewText'


describe('previewText strips marks a one-line preview must not leak', () => {
  it('drops emphasis, heading, list, quote, link and fence marks but keeps the words', () => {
    const md = [
      '# RAIDZ2 vs dRAID',
      '',
      '**Recommendation: use RAIDZ2.** At 6–12 disks *modestly* faster.',
      '> quoted line',
      '- a bullet',
      '2. numbered',
      '[a link](https://example.test/x) and `code`',
      '```ts',
      'const x = 1',
      '```',
    ].join('\n')
    const out = previewText(md)
    expect(out).toContain('RAIDZ2 vs dRAID')
    expect(out).toContain('Recommendation: use RAIDZ2.')
    expect(out).toContain('a link and code')
    expect(out).toContain('const x = 1')
    for (const mark of ['**', '##', '# ', '> ', '- ', '](', '```', '`', '*']) {
      expect(out, `mark ${JSON.stringify(mark)} must be stripped`).not.toContain(mark)
    }
  })

  it('collapses to one line and honors the cap with an ellipsis', () => {
    expect(previewText('a\n\nb\n c')).toBe('a b c')
    const long = previewText('word '.repeat(50), 55)
    expect(long.length).toBeLessThanOrEqual(55)
    expect(long.endsWith('…')).toBe(true)
  })

  it('is safe on empty and null-ish input', () => {
    expect(previewText('')).toBe('')
    expect(previewText(null)).toBe('')
    expect(previewText(undefined)).toBe('')
  })
})

describe('both leak surfaces route through the strip point (source-level)', () => {
  const inbox = readFileSync(resolve(__dirname, "../../features/inbox/InboxPage.tsx"), 'utf8')
  const card = readFileSync(resolve(__dirname, "../../features/artifacts/ArtifactCard.tsx"), 'utf8')

  it('the inbox row preview and its label both strip the message', () => {
    expect(inbox).toContain("import { previewText } from '../../shared/data/previewText'")
    expect(inbox).toMatch(/<p[^>]*truncate[^>]*>\{previewText\(it\.message\)\}<\/p>/)
    expect(inbox).toMatch(/rowSubject\(\[[^\]]*previewText\(it\.message\)\]/)
  })

  it('the artifact excerpt is a decorative thumbnail, hidden from the accessible tree', () => {
    const excerpt = card.slice(card.indexOf('function ExcerptPreview'))
    expect(excerpt.slice(0, 600)).toContain('aria-hidden')
  })
})
