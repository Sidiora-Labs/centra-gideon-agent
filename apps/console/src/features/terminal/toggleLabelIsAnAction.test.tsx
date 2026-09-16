import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src/features/terminal/TerminalPage.tsx")
const raw = readFileSync(SRC, 'utf8')
const src = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the terminal persistence toggle is labelled as an action', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(raw).toMatch(/HeaderControl icon=\{Anchor\}/)
    expect(raw.length).toBeGreaterThan(3000)
  })

  it('both states are imperative, and neither is a status sentence', () => {
    expect(src).toMatch(/label=\{persist \? 'Disable persistent sessions' : 'Enable persistent sessions'\}/)
    expect(/Persistent sessions (on|off) —/.test(src), 'the label must not report state').toBe(false)
  })

  it('the explanation lives in hint, so it survives without bloating the name', () => {
    expect(src).toMatch(/hint=\{persist/)
    expect(src, 'the tmux detail must not be lost').toMatch(/tmux/)
  })

  it('state is still conveyed — via active, not via the words', () => {
    expect(src).toMatch(/active=\{persist\}/)
  })

  it('matches the shape the Files header already uses', () => {
    const files = readFileSync(join(process.cwd(), "src/features/files/FilesSection.tsx"), 'utf8')
    expect(files).toMatch(/label=\{explorerOpen \? 'Hide explorer' : 'Show explorer'\}/)
  })
})
