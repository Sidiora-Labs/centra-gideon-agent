import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const inbox = readFileSync(join(SRC, 'features/inbox/InboxPage.tsx'), 'utf8')
const inboxCode = inbox.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the inbox distinguishes "nothing matches" from "you have nothing"', () => {
  it('derives `narrowed` once, against its own default filter', () => {
    expect(inbox).toMatch(/const narrowed = !!\(q\.trim\(\) \|\| filter !== 'open' \|\| kind\)/)
    expect(inboxCode, "'all' is not this surface's default — 'open' is").not.toMatch(/filter !== 'all'/)
  })

  it('the narrowed hint tells the user about their filter, not about onboarding', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag, 'the empty state must still exist').toContain('InboxIcon')
    expect(tag, 'narrowed must be tested BEFORE disabled').toMatch(
      /hint=\{narrowed[\s\S]*?:\s*disabled/,
    )
    expect(tag).toMatch(/Try a different search or filter\./)
  })

  it('keeps the blank-slate copy for the state it was written for', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag).toMatch(/Enable a source to begin\./)
    expect(tag, 'and the caught-up line for a genuinely empty, enabled inbox').toMatch(/all caught up/)
  })

  it('keeps the kind-specific narrowed line, and makes it say why', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag).toMatch(/matches the current search or filter\./)
  })

  it('the title and the hint read the SAME flags, in the same order, so they cannot disagree', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag, 'the tag must be found before it can be measured').not.toBe('')
    expect(tag, 'the title must branch narrowed → disabled → caught-up')
      .toMatch(/title=\{narrowed \? 'Nothing here' : disabled \? '[^']+' : 'Inbox zero'\}/)
    expect(tag, 'and the hint must test the same two flags in the same order')
      .toMatch(/hint=\{narrowed[\s\S]*?: disabled/)
  })

  it('the announcement shares that one definition too', () => {
    expect(inbox).toMatch(/results=\{\{[^}]*active: narrowed[^}]*\}\}/)
  })

  it('the eleven surfaces that were already right still are', () => {
    const CANONICAL: [string, RegExp][] = [
      ['features/artifacts/ArtifactGrid.tsx', /Try a different search, kind, or collection\./],
      ['features/knowledge/KnowledgeListPage.tsx', /No items match “\$\{submitted\}”/],
      ['features/prompts/PromptsListPage.tsx', /Try a different term\./],
      ['features/skills/SkillsPage.tsx', /Try a different term\./],
      ['features/triggers/TriggersListPage.tsx', /Try a different filter\./],
      ['features/workflows/WorkflowsListPage.tsx', /Try a different search\./],
      ['features/tasks/TasksListPage.tsx', /No tasks match this (filter|scope)\./],
    ]
    for (const [rel, copy] of CANONICAL) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must keep its narrowed copy`).toMatch(copy)
    }
  })
})
