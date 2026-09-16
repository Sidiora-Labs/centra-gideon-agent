import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")

const CREATE_PAGES = [
  join('tasks', 'TaskCreatePage.tsx'),
  join('triggers', 'TriggerCreatePage.tsx'),
  join('agents', 'AgentCreatePage.tsx'),
  join('prompts', 'PromptCreatePage.tsx'),
  join('knowledge', 'KnowledgeCreatePage.tsx'),
]

describe('a create form announces its submit failure', () => {
  it.each(CREATE_PAGES)('%s marks the error as an alert', (rel) => {
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src, `${rel} must render a submit error at all`).toMatch(/\{err && </)
    const at = src.indexOf('{err && <')
    const tag = src.slice(at, at + 200)
    expect(tag, `${rel}: an unrequested failure must be announced`).toMatch(/role="alert"/)
  })

  it.each(CREATE_PAGES.slice(0, 4))('%s scrolls the error into view', (rel) => {
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src, `${rel}: the message must be reachable without hunting for it`).toMatch(
      /errRef\.current\?\.scrollIntoView\(\{ block: 'nearest' \}\)/,
    )
    expect(src, `${rel}: and the ref must be attached to the message`).toMatch(/ref=\{errRef\}/)
  })

  it('the scroll fires on the error appearing, not on every render', () => {
    for (const rel of CREATE_PAGES.slice(0, 4)) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      expect(src, `${rel}: guard on err and depend on it`).toMatch(/useEffect\(\(\) => \{ if \(err\) errRef/)
      expect(src, `${rel}: the effect must depend on err`).toMatch(/\}, \[err\]\)/)
    }
  })
})
