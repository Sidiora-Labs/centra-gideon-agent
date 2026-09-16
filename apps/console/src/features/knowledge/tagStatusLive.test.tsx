import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/knowledge/TagManager.tsx"), 'utf8')

describe('the tag panel announces what it just did', () => {
  it('the live region is always mounted, polite, and sr-only', () => {
    expect(SRC).toMatch(/<span role="status" aria-live="polite" className="sr-only">\{note\}<\/span>/)
  })

  it('the region is not gated behind the note it carries', () => {
    expect(SRC, 'no conditionally-mounted status region remains')
      .not.toMatch(/\{note && \([\s\S]{0,120}role="status"/)
  })

  it('the visible line is hidden from the tree, so the sentence is announced once', () => {
    expect(SRC).toMatch(/\{note && \([\s\S]{0,80}aria-hidden="true"/)
    const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    const statuses = [...code.matchAll(/role="status"/g)]
    expect(statuses.length, 'exactly one status region in this panel').toBe(1)
  })

  it('every operation still feeds it a real sentence — the vacuity floor', () => {
    expect(SRC, 'rename').toMatch(/run\(t\.id, `Renamed “\$\{t\.name\}”`/)
    expect(SRC, 'make top-level / nest').toMatch(/is now top-level` : `Moved “\$\{t\.name\}”`/)
    expect(SRC, 'merge').toMatch(/`Merged “\$\{t\.name\}” into “\$\{into\.name\}”`/)
    expect(SRC, 'delete').toMatch(/run\(t\.id, `Deleted “\$\{t\.name\}”`/)
    expect(SRC).toMatch(/setNote\(label\)/)
  })

  it('matches the shape AudioRecorder is already held to', () => {
    const sibling = readFileSync(join(process.cwd(), "src/features/knowledge/AudioRecorder.tsx"), 'utf8')
    expect(sibling, 'the precedent still ships the always-mounted region')
      .toMatch(/role="status" aria-live="polite"[^>]*sr-only|sr-only[^>]*role="status"/)
  })
})
