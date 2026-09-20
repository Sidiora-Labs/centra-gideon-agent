import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const page = readFileSync(join(process.cwd(), 'src/features/knowledge/KnowledgeListPage.tsx'), 'utf8')
const api = readFileSync(join(process.cwd(), 'src/shared/data/api.ts'), 'utf8')
const backend = readFileSync(join(process.cwd(), '../../runtime/gideon/interfaces/dashboard/handlers/knowledge.py'), 'utf8')

describe('editable knowledge intents', () => {
  it('wires Pause and Propose skill through the partial-update API', () => {
    expect(page).toContain("update({ enabled: !intent.enabled })")
    expect(page).toContain("update({ propose_skill: !intent.propose_skill })")
    expect(page).toContain("intent.enabled ? 'Pause' : 'Resume'")
    expect(page).toContain("intent.propose_skill ? 'Stop proposing skill' : 'Propose skill'")
    expect(api).toMatch(/updateKnowledgeIntent:[\s\S]*patch<\{ intent: KnowledgeIntent \}>/)
  })

  it('registers a boolean-only backend PATCH contract', () => {
    expect(backend).toContain('async def update_intent(')
    expect(backend).toContain('allowed = {"enabled", "propose_skill"}')
    expect(backend).toContain('any(not isinstance(value, bool) for value in body.values())')
    expect(backend).toContain('app.router.add_patch("/api/knowledge/intents/{id}", update_intent)')
  })
})
