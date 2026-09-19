import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


describe('the reading breadcrumb focus ring', () => {
  it('draws inside the back control so overflow clipping cannot hide it', () => {
    const source = readFileSync(join(process.cwd(), 'src/features/knowledge/KnowledgeReadingPage.tsx'), 'utf8')
    const backControl = source.match(/<IconButton icon=\{ArrowLeft\} label="Back to knowledge item"[\s\S]*?\/>/)?.[0]

    expect(backControl, 'the reading breadcrumb must keep its back control').toBeTruthy()
    expect(backControl).toContain('className="focus-visible:ring-inset"')
  })
})
