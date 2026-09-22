import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(process.cwd(), 'src')
const read = (path: string) => readFileSync(join(SRC, path), 'utf8')

describe('surface width tokens', () => {
  const appearance = read('app/shell/appearance.tsx')

  it('publishes an explicit width token for every wider surface', () => {
    expect(appearance).toMatch(/--modal-width.*160px/)
    expect(appearance).toMatch(/--side-panel-width.*200px/)
    expect(appearance).toMatch(/--snip-overlay-width.*320px/)
    expect(appearance).toMatch(/--loop-cockpit-width.*340px/)
  })

  it.each([
    ['shared/ui/Modal.tsx', '--modal-width'],
    ['shared/ui/SidePanel.tsx', '--side-panel-width'],
    ['shared/ui/SnipOverlay.tsx', '--snip-overlay-width'],
    ['features/loops/LoopCockpitPage.tsx', '--loop-cockpit-width'],
  ])('%s consumes %s instead of offsetting the content width', (path, token) => {
    const source = read(path)
    expect(source).toContain(`var(${token})`)
    expect(source).not.toMatch(/calc\(var\(--content-width\)\s*\+\s*\d+px\)/)
  })
})
