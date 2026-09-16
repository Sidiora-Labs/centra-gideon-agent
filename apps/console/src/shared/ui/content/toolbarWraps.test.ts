import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const FILE = join(process.cwd(), "src/shared/ui/content/ContentSurface.tsx")

function code(): string {
  return readFileSync(FILE, 'utf8')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
}

function tags(src: string): { toolbar: string; cluster: string } {
  const toolbar = src.match(/<div className="[^"]*\bborder-b border-outline\/40 px-m py-1\.5[^"]*">/)
  const cluster = src.match(/<div className="[^"]*\bml-auto\b[^"]*\bgap-1\b[^"]*">/)
  expect(toolbar?.[0], 'the toolbar row opening tag was not found — this rail is measuring nothing').toBeTruthy()
  expect(cluster?.[0], 'the ml-auto action cluster was not found — this rail is measuring nothing').toBeTruthy()
  return { toolbar: toolbar![0], cluster: cluster![0] }
}

describe('ContentSurface toolbar survives a phone width', () => {
  it('the toolbar row wraps', () => {
    const { toolbar } = tags(code())
    expect(toolbar, `the toolbar row must wrap: ${toolbar}`).toMatch(/\bflex-wrap\b/)
  })

  it('the wrap is not gated on `compact` — density is not available space', () => {
    const src = code()
    expect(src, 'flex-wrap must not be conditional on compact')
      .not.toMatch(/compact\s*\?\s*'flex-wrap'/)
  })

  it('the ml-auto action cluster wraps too, and stays right-aligned when it does', () => {
    const { cluster } = tags(code())
    expect(cluster, `the action cluster must wrap: ${cluster}`).toMatch(/\bflex-wrap\b/)
    expect(cluster, `wrapped lines must stay flush right: ${cluster}`).toMatch(/\bjustify-end\b/)
  })

  it('the scanned source is real (guard against a vacuous pass)', () => {
    const src = code()
    expect(src).toContain('<ToggleBtn')
    expect(src).toContain('showToolbar &&')
    expect(src).not.toContain(['available', 'space'].join(' ').toUpperCase())
  })
})
