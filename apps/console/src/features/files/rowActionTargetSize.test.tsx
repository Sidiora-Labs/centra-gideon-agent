import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


describe('the file-tree row-action trigger', () => {
  const src = readFileSync(join(process.cwd(), "src/features/files/browse/FileTree.tsx"), 'utf8')

  it('clicks 24px', () => {
    expect(src).toMatch(/grid size-6 place-items-center rounded text-on-surface-low/)
  })

  it('absorbs the extra width in its offset, so the glyph does not move', () => {
    expect(src).toMatch(/absolute right-0\.5 top-1\/2 -translate-y-1\/2 grid size-6/)
    expect(src, 'the old offset would move the paint').not.toMatch(/absolute right-1 top-1\/2 -translate-y-1\/2 grid size-6/)
  })

  it('keeps the 13px glyph — the fix is the hit box, not the design', () => {
    expect(src).toMatch(/<MoreHorizontal size=\{13\} \/>/)
  })

  it('stays hidden until the row is hovered or focused', () => {
    expect(src).toMatch(/opacity-0 transition-opacity[^"]*focus-visible:opacity-100 group-hover\/row:opacity-100/)
  })
})
