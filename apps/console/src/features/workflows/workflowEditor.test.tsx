import { describe, expect, it } from 'vitest'
import type { WorkflowDef } from '../../shared/data/api'
import { editableWorkflow, parseWorkflowJson, replaceStep, workflowJson, workflowSteps } from './defEditing'

const definition: WorkflowDef = {
  name: 'code-project', version: 4, source: 'bundled', description: 'Build a change',
  root: { kind: 'sequence', id: 'main', children: [{ kind: 'action', id: 'test', config: { provider: 'shell', with: { command: 'make test' }, max_tokens: 40 } }] },
  inputs: { task: { type: 'string', required: true } }, tags: ['engineering'],
  defaults: { budget: { max_tokens: 0 } }, on_overlap: 'queue', runtime_hints: { judge: { rubric: 'Keep the shipped review contract' } },
  workspace: { root: 'project' }, metadata: { requirements: { tools: ['shell'] } },
}

describe('workflow editor document', () => {
  it('keeps every authored field when moving between Steps and JSON', () => {
    const editable = editableWorkflow(definition)
    const parsed = parseWorkflowJson(workflowJson(editable))
    expect('document' in parsed).toBe(true)
    if (!('document' in parsed)) throw new Error(parsed.error)
    expect(parsed.document).toEqual(editable)
    expect(editable.defaults).toEqual(definition.defaults)
    expect(editable.on_overlap).toBe('queue')
    expect(editable.runtime_hints).toEqual(definition.runtime_hints)
    expect(editable.workspace).toEqual(definition.workspace)
  })

  it('addresses and replaces the full step without changing its sibling', () => {
    const rows = workflowSteps(definition.root)
    expect(rows.map((row) => row.path)).toEqual(['root', 'root.children[0]'])
    const replacement = { ...rows[1].node, config: { ...rows[1].node.config, provider: 'http' } }
    const changed = replaceStep(definition.root, rows[1].hops, replacement)
    expect(changed.children?.[0].config?.provider).toBe('http')
    expect(definition.root.children?.[0].config?.provider).toBe('shell')
  })

  it('keeps case labels structured even when their punctuation looks like path syntax', () => {
    const root = { kind: 'branch', id: 'route', cases: { 'hit].label': { kind: 'transform', id: 'case-step', config: { expr: 'before' } } } }
    const row = workflowSteps(root)[1]
    const changed = replaceStep(root, row.hops, { ...row.node, config: { expr: 'after' } })
    expect(changed.cases?.['hit].label'].config?.expr).toBe('after')
  })
})
