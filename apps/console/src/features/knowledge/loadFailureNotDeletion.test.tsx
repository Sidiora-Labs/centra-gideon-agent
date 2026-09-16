import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a failed read is not a deletion', () => {
  it('knowledge-detail claims deletion ONLY on a 404', () => {
    const code = read('features/knowledge/KnowledgeDetailPage.tsx')
    expect(code, 'the status has to survive, so the API is called directly')
      .toMatch(/api\.knowledgeItem\(id\)/)
    expect(code).toMatch(/if \(e instanceof ApiError && e\.status === 404\) setMissing\(true\)/)
    expect(code, 'anything else is a load failure').toMatch(/else setLoadErr\(e\)/)
  })

  it('knowledge-detail offers a retry instead of asserting a deletion', () => {
    const code = read('features/knowledge/KnowledgeDetailPage.tsx')
    expect(code).toMatch(/loadErr \? \(/)
    expect(code).toMatch(/<LoadError what="knowledge item" error=\{loadErr\} onRetry=/)
    expect(code).toMatch(/This knowledge item no longer exists\./)
  })

  it("knowledge-detail's header does not say Not found while a read is merely failing", () => {
    expect(read('features/knowledge/KnowledgeDetailPage.tsx'))
      .toMatch(/missing \? 'Not found' : loadErr \? "Couldn't load" : 'Loading…'/)
  })

  it('projects-detail stops discarding the load error', () => {
    const code = read('features/projects/ProjectsSection.tsx')
    expect(code, 'the error must be destructured to be checkable')
      .toMatch(/const \{ data: project, loading, error: detailErr, refresh \} = useQuery/)
    expect(code, 'and a failed read renders the retry, before the deletion branch')
      .toMatch(/if \(!project && detailErr\) \{[\s\S]{0,220}?<LoadError what="project" error=\{detailErr\} onRetry=\{refresh\} \/>/)
  })

  it('projects-detail keeps the deletion copy for a genuine absence', () => {
    const code = read('features/projects/ProjectsSection.tsx')
    expect(code).toMatch(/This project no longer exists\./)
    expect(code.indexOf('if (!project && detailErr)'))
      .toBeLessThan(code.indexOf('This project no longer exists.'))
  })

  it('the retry copy does not double the article ("your this project")', () => {
    for (const rel of ['features/knowledge/KnowledgeDetailPage.tsx', 'features/projects/ProjectsSection.tsx']) {
      expect(read(rel), `${rel} must not pass a demonstrative to LoadError`)
        .not.toMatch(/<LoadError what="(this|your|the) /)
    }
  })

  it('the 404-vs-other rule this converges on is still the repo’s rule', () => {
    expect(read('features/projects/ProjectsSection.tsx'))
      .toMatch(/if \(status === 404\) return "This folder no longer exists on disk\."/)
  })

  it('the store contract its other callers rely on is untouched', () => {
    expect(read('features/knowledge/knowledgeStore.ts'))
      .toMatch(/try \{ return await api\.knowledgeItem\(id\) \} catch \{ return null \}/)
  })
})
