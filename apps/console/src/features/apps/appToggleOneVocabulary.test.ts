import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'


const SRC = resolve(__dirname, "AppsSection.tsx")

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const code = stripComments(readFileSync(SRC, 'utf8'))

describe('one toggle, one vocabulary (issue 617)', () => {
  it('the split-vocabulary labels are gone from every surface', () => {
    expect(code).not.toContain('Install (activate)')
    expect(code).not.toContain('Uninstall (deactivate)')
  })

  it('every toggle surface renders the shared Activate/Deactivate pair', () => {
    const pair = /enabled\s*\?\s*'Deactivate'\s*:\s*'Activate'/g
    expect((code.match(pair) ?? []).length).toBeGreaterThanOrEqual(3)
    expect(code).toContain('Activate</Button>')
  })

  it("the toggle never wears 'Install' or 'Uninstall' — those imply file movement", () => {
    expect(code).not.toMatch(/enabled\s*\?\s*'Uninstall'\s*:\s*'Install'/)
    expect(code).toContain('Force uninstall')
  })
})
