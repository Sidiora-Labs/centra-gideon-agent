import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { FieldError } from './forms'
import { InlineError } from './InlineError'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('FieldError', () => {
  it('announces — that is the whole reason it exists', () => {
    const { container } = render(<FieldError>Could not save</FieldError>)
    expect(container.querySelector('[role="alert"]'), 'a failure the user did not request must interrupt').not.toBeNull()
  })

  it('renders the same line it replaced, so nothing moves', () => {
    const { container } = render(<FieldError>Could not save</FieldError>)
    const p = container.querySelector('p')!
    expect(p.className).toBe('text-danger')
    expect(p.getAttribute('data-type')).toBe('body-s')
    expect(p.textContent).toBe('Could not save')
  })

  it('takes per-site spacing without letting a site re-tone it', () => {
    const { container } = render(<FieldError className="mt-2">x</FieldError>)
    expect(container.querySelector('p')!.className).toBe('text-danger mt-2')
  })

  it('agrees with InlineError that a failure is an alert', () => {
    const { container } = render(<InlineError>boom</InlineError>)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
  })
})

describe('no site hand-rolls the silent line any more', () => {
  const files = walk(SRC)
  const RAW = /<(p|div|span)[^>]*className="(?:[a-z0-9:.\-[\]/]+ )*text-danger text-\[0\.(?:8125|75)rem\]"[^>]*>\{\s*\w[\w.]*\s*\}<\/\1>/g

  const offenders = files.flatMap((f) => {
    const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    return [...src.matchAll(RAW)]
      .filter((m) => !/role="alert"/.test(m[0]))
      .map((m) => ({ file: f.slice(SRC.length + 1), snippet: m[0].slice(0, 70) }))
      .filter((o) => o.file !== 'features/workflows/WorkflowRunDetail.tsx')
  })

  it('finds the primitive at the sites that used to hand-roll it (not vacuously green)', () => {
    const adopters = files.filter((f) => /<FieldError\b/.test(readFileSync(f, 'utf8')))
    expect(adopters.length, 'the conversion must actually be there').toBeGreaterThanOrEqual(18)
  })

  it('has no silent failure line left', () => {
    expect(offenders.map((o) => o.file), 'a failure rendered with no role announces to nobody').toEqual([])
  })
})
