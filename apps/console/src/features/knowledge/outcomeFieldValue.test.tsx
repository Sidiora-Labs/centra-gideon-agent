import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { OutcomeFieldValue } from './KnowledgeDetail'


const KNOWLEDGE = join(process.cwd(), "src/features/knowledge")

describe('OutcomeFieldValue is defined once', () => {
  it('no other file in the knowledge area declares it', () => {
    const definers = readdirSync(KNOWLEDGE)
      .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))
      .filter((f) => /function OutcomeFieldValue\b/.test(readFileSync(join(KNOWLEDGE, f), 'utf8')))
    expect(definers, `declared in: ${definers.join(', ')}`).toEqual(['KnowledgeDetail.tsx'])
  })

  it('the list page imports it rather than re-declaring', () => {
    const src = readFileSync(join(KNOWLEDGE, 'KnowledgeListPage.tsx'), 'utf8')
    expect(src).toMatch(/import \{[^}]*OutcomeFieldValue[^}]*\} from '\.\/KnowledgeDetail'/)
  })
})

describe('the dispatch table itself', () => {
  const cases: Array<[string, { type: string; value: unknown }, (t: string) => void]> = [
    ['empty string → em dash', { type: 'text', value: '' }, (t) => expect(t).toBe('—')],
    ['null → em dash', { type: 'text', value: null }, (t) => expect(t).toBe('—')],
    ['boolean true → Yes', { type: 'boolean', value: true }, (t) => expect(t).toBe('Yes')],
    ['boolean false → No', { type: 'boolean', value: false }, (t) => expect(t).toBe('No')],
    ['number → the digits', { type: 'number', value: 42 }, (t) => expect(t).toBe('42')],
    ['unknown type → String(value)', { type: 'wat', value: 'raw' }, (t) => expect(t).toBe('raw')],
  ]

  for (const [name, field, assert] of cases) {
    it(name, () => {
      const { container } = render(<OutcomeFieldValue field={field as never} />)
      assert((container.textContent ?? '').trim())
    })
  }

  it('url renders a safe external link', () => {
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'url', value: 'https://example.com/x' } as never} />,
    )
    const a = container.querySelector('a')
    expect(a?.getAttribute('href')).toBe('https://example.com/x')
    expect(a?.getAttribute('target')).toBe('_blank')
    expect(a?.getAttribute('rel')).toContain('noreferrer')
  })

  it('tags render one chip per entry', () => {
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'tags', value: ['a', 'b', 'c'] } as never} />,
    )
    expect(container.querySelectorAll('span.rounded-pill')).toHaveLength(3)
  })

  it('a tags TYPE with a non-array value falls through instead of crashing', () => {
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'tags', value: 'not-an-array' } as never} />,
    )
    expect((container.textContent ?? '').trim()).toBe('not-an-array')
  })
})
