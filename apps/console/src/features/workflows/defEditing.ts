import type { WorkflowDef, WorkflowNode } from '../../shared/data/api'

const BOOKKEEPING = new Set(['name', 'version', 'spec_semver', 'source', 'provenance', 'created_at', 'updated_at'])
export type EditableWorkflow = Omit<WorkflowDef, 'name' | 'version' | 'root'> & { root: WorkflowNode; [key: string]: unknown }

export function editableWorkflow(definition: WorkflowDef): EditableWorkflow {
  const fields: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(definition)) if (!BOOKKEEPING.has(key)) fields[key] = value
  return { ...fields, description: definition.description ?? '', inputs: definition.inputs ?? {}, tags: definition.tags ?? [], metadata: definition.metadata ?? {}, root: definition.root } as EditableWorkflow
}

export function workflowJson(document: EditableWorkflow): string { return JSON.stringify(document, null, 2) }
export function workflowEditAction(source: string | undefined): 'edit' | 'copy' { return source === 'bundled' ? 'copy' : 'edit' }
export function restoreEntry(version: number): { restoreVersion: number } { return { restoreVersion: version } }

export function parseWorkflowJson(source: string): { document: EditableWorkflow } | { error: string } {
  let value: unknown
  try { value = JSON.parse(source) } catch (error) { return { error: error instanceof Error ? error.message : 'Invalid JSON.' } }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: 'The workflow must be a JSON object.' }
  const doc = value as Record<string, unknown>
  const root = doc.root
  if (!root || typeof root !== 'object' || Array.isArray(root) || typeof (root as { kind?: unknown }).kind !== 'string') return { error: 'The workflow needs a root step with a kind.' }
  return { document: { ...doc, description: typeof doc.description === 'string' ? doc.description : '', inputs: doc.inputs && typeof doc.inputs === 'object' && !Array.isArray(doc.inputs) ? doc.inputs as Record<string, unknown> : {}, tags: Array.isArray(doc.tags) ? doc.tags.map(String) : [], metadata: doc.metadata && typeof doc.metadata === 'object' && !Array.isArray(doc.metadata) ? doc.metadata as Record<string, unknown> : {}, root: root as WorkflowNode } as EditableWorkflow }
}

export type WorkflowHop = { at: 'child'; index: number } | { at: 'body' } | { at: 'default' } | { at: 'case'; label: string }
export interface WorkflowStep { path: string; node: WorkflowNode; depth: number; hops: WorkflowHop[] }
export function workflowSteps(root: WorkflowNode): WorkflowStep[] {
  const out: WorkflowStep[] = []
  const visit = (node: WorkflowNode, path: string, depth: number, hops: WorkflowHop[]) => {
    out.push({ path, node, depth, hops })
    ;(node.children ?? []).forEach((child, index) => visit(child, `${path}.children[${index}]`, depth + 1, [...hops, { at: 'child', index }]))
    if (node.body) visit(node.body, `${path}.body`, depth + 1, [...hops, { at: 'body' }])
    for (const [label, child] of Object.entries(node.cases ?? {})) visit(child, `${path}.cases[${label}]`, depth + 1, [...hops, { at: 'case', label }])
    if (node.default) visit(node.default, `${path}.default`, depth + 1, [...hops, { at: 'default' }])
  }
  visit(root, 'root', 0, [])
  return out
}

export function replaceStep(root: WorkflowNode, hops: WorkflowHop[], replacement: WorkflowNode): WorkflowNode {
  const recur = (node: WorkflowNode, index: number): WorkflowNode => {
    if (index >= hops.length) return replacement
    const hop = hops[index]
    if (hop.at === 'child') { const children = [...(node.children ?? [])]; children[hop.index] = recur(children[hop.index], index + 1); return { ...node, children } }
    if (hop.at === 'body') return { ...node, body: recur(node.body as WorkflowNode, index + 1) }
    if (hop.at === 'default') return { ...node, default: recur(node.default as WorkflowNode, index + 1) }
    return { ...node, cases: { ...(node.cases ?? {}), [hop.label]: recur((node.cases ?? {})[hop.label], index + 1) } }
  }
  return recur(root, 0)
}
