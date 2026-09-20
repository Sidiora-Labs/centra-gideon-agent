import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { seedRenderValues } from './prompts/promptMeta'
import { emptyDiscoverReason } from './discover/DiscoverPage'

const read = (path: string) => readFileSync(join(process.cwd(), 'src/features', path), 'utf8')

describe('six honest UI surfaces', () => {
  it('omits unset typed prompt values instead of inventing rejected values', () => {
    expect(seedRenderValues([
      { name: 'count', type: 'number' },
      { name: 'enabled', type: 'boolean' },
      { name: 'mode', type: 'select', default: 'safe' },
    ])).toEqual({ mode: 'safe' })
  })

  it('allocates paste markers from a synchronous ref so consecutive pastes cannot share one', () => {
    const page = read('ChatPage.tsx')
    expect(page).toContain('const seq = ++pasteSeq.current')
    expect(page).not.toContain('const seq = nextSeq(pasteBlocks)')
  })

  it('shows the resolved explorer path and renders a refused read as an error', () => {
    expect(read('files/FilesSection.tsx')).toContain('dirs.resolved[activeRoot] || activeRoot')
    expect(read('files/browse/FileTree.tsx')).toContain('Couldn&rsquo;t open this path:')
  })

  it('does not promise automatic naming while requiring a name', () => {
    const page = read('projects/ProjectsSection.tsx')
    expect(page).toContain('placeholder="Project name"')
    expect(page).toContain('label="Rename project"')
    expect(page).toContain('aria-label="Automatic naming off"')
  })

  it('names why Discover is empty', () => {
    expect(emptyDiscoverReason(2, 3)).toContain('3 hidden because you tried')
    expect(emptyDiscoverReason(2, 0)).toContain('dismissed 2 tips')
    const backend = readFileSync(join(process.cwd(), '../../runtime/gideon/assurance/legibility/discover.py'), 'utf8')
    expect(backend).toContain('"dismissed_count": dismissed_count')
    expect(backend).toContain('"engaged_count": engaged_count')
  })
})
