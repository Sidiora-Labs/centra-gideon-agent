import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import { gatewayJson } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import type { WorkflowDef, WorkflowDefSummary, WorkflowNode } from '../../../../console/src/shared/data/api'
import WorkflowNodeInspector, { type WorkflowEditableNode } from './WorkflowNodeInspector.web'

type Definition = WorkflowDef & { name: string; version?: number; metadata?: WorkflowDef['metadata'] }
type Issue = { code?: string; message?: string; path?: string; severity?: string }
type Response = { definition?: Definition; defs?: WorkflowDefSummary[]; saved?: boolean; valid?: boolean;
  issues?: Issue[]; run_id?: string; status?: string; a2a_published?: boolean }
type Segment = { key: 'children'; index: number } | { key: 'body' } | { key: 'default' }
  | { key: 'cases'; name: string }
type GraphNode = { node: WorkflowNode; path: Segment[]; key: string; label: string; id: string }

const buttonStyle: React.CSSProperties = {
  minHeight: 42, border: '1px solid color-mix(in srgb, currentColor 24%, transparent)',
  borderRadius: 9, background: 'transparent', color: 'inherit', padding: '7px 11px',
  font: 'inherit', cursor: 'pointer', textAlign: 'left',
}
const enc = (value: string) => encodeURIComponent(value)
const emptyDefinition = (name = ''): Definition => ({ name, description: '', version: 0,
  root: { kind: 'sequence', id: 'workflow', children: [] }, inputs: {}, tags: [], metadata: {} })

function graphOf(root: WorkflowNode): GraphNode[] {
  const result: GraphNode[] = []
  const visit = (node: WorkflowNode, path: Segment[], pathText: string, branch = '') => {
    const labeled = node as WorkflowNode & { label?: string }
    const id = typeof node.id === 'string' && node.id ? node.id : `node-${pathText || 'root'}`
    const label = typeof labeled.label === 'string' && labeled.label ? labeled.label : `${node.kind} · ${id}`
    result.push({ node, path, key: pathText || 'root', label: branch ? `${branch}: ${label}` : label, id })
    node.children?.forEach((child, index) => visit(child, [...path, { key: 'children', index }], `${pathText}/children/${index}`))
    if (node.body) visit(node.body, [...path, { key: 'body' }], `${pathText}/body`, 'body')
    for (const [name, child] of Object.entries(node.cases ?? {}))
      visit(child, [...path, { key: 'cases', name }], `${pathText}/cases/${name}`, `case ${name}`)
    const fallback = (node as WorkflowNode & { default_case?: WorkflowNode }).default ??
      (node as WorkflowNode & { default_case?: WorkflowNode }).default_case
    if (fallback) visit(fallback, [...path, { key: 'default' }], `${pathText}/default`, 'default')
  }
  visit(root, [], '')
  return result
}

function updateNode(root: WorkflowNode, path: Segment[], change: (node: WorkflowNode) => WorkflowNode): WorkflowNode {
  if (!path.length) return change(root)
  const [segment, ...rest] = path
  if (segment.key === 'children') {
    const children = [...(root.children ?? [])]
    children[segment.index] = updateNode(children[segment.index], rest, change)
    return { ...root, children }
  }
  if (segment.key === 'body') return root.body ? { ...root, body: updateNode(root.body, rest, change) } : root
  if (segment.key === 'default') {
    const value = (root as WorkflowNode & { default_case?: WorkflowNode }).default ??
      (root as WorkflowNode & { default_case?: WorkflowNode }).default_case
    if (!value) return root
    const updated = updateNode(value, rest, change)
    return { ...root, default: updated }
  }
  const cases = { ...(root.cases ?? {}) }
  if (cases[segment.name]) cases[segment.name] = updateNode(cases[segment.name], rest, change)
  return { ...root, cases }
}

function editableNode(item: GraphNode): WorkflowEditableNode {
  const config = item.node.config ?? {}
  return { id: item.id, kind: item.node.kind, label: (item.node as WorkflowNode & { label?: string }).label ?? '',
    prompt: typeof config.prompt === 'string' ? config.prompt : '', needs: item.node.needs ?? [] }
}

export type WorkflowEditorProps = Readonly<{
  scope: OwnerScope
  initialName?: string
  route?: ShellRoute
  onBack?: () => void
  onDefinitionSelected?: (name: string) => void
  onDefinitionSaved?: (name: string) => void
}>

export default function WorkflowEditor({ scope, initialName, route, onBack, onDefinitionSelected, onDefinitionSaved }: WorkflowEditorProps) {
  const generation = useRef({ cacheKey: scope.cacheKey, value: 0 })
  const operation = useRef(0)
  if (generation.current.cacheKey !== scope.cacheKey)
    generation.current = { cacheKey: scope.cacheKey, value: generation.current.value + 1 }
  const [definitions, setDefinitions] = useState<WorkflowDefSummary[]>([])
  const [selectedName, setSelectedName] = useState(initialName ?? '')
  const [definition, setDefinition] = useState<Definition | null>(null)
  const [draft, setDraft] = useState<Definition | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [issues, setIssues] = useState<Issue[]>([])
  const [notice, setNotice] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [newName, setNewName] = useState('')
  const [stateCacheKey, setStateCacheKey] = useState(scope.cacheKey)
  const current = useRef({ cacheKey: scope.cacheKey, selectedName, definition, draft })
  current.current = { cacheKey: scope.cacheKey, selectedName, definition, draft }
  const ownerMatches = stateCacheKey === scope.cacheKey
  const visibleDefinitions = ownerMatches ? definitions : []
  const visibleSelectedName = ownerMatches ? selectedName : ''
  const visibleDefinition = ownerMatches ? definition : null
  const visibleDraft = ownerMatches ? draft : null
  const isCurrent = (token: number, snapshot: typeof current.current) =>
    generation.current.value === token && current.current.cacheKey === snapshot.cacheKey &&
    current.current.selectedName === snapshot.selectedName && current.current.definition === snapshot.definition &&
    current.current.draft === snapshot.draft

  const refreshList = useCallback(async (token = generation.current.value) => {
    setLoading(true); setError('')
    try {
      const result = await gatewayJson<Response>('/api/workflows')
      if (generation.current.value !== token || generation.current.cacheKey !== scope.cacheKey) return
      const rows = Array.isArray(result.defs) ? result.defs : []
      setDefinitions(rows)
      if (!rows.length && !current.current.draft) { setDefinition(null); setDraft(null) }
    } catch (cause) {
      if (generation.current.value === token && generation.current.cacheKey === scope.cacheKey)
        setError(cause instanceof Error ? cause.message : 'Workflow definitions could not be loaded.')
    } finally {
      if (generation.current.value === token && generation.current.cacheKey === scope.cacheKey) setLoading(false)
    }
  }, [scope.cacheKey])

  useEffect(() => {
    const token = generation.current.value
    setStateCacheKey(scope.cacheKey)
    setDefinitions([]); setSelectedName(initialName ?? ''); setDefinition(null); setDraft(null); setLoading(true)
    setBusy(false); setError(''); setIssues([]); setNotice(''); setSelectedKey(''); setNewName('')
    void refreshList(token)
  }, [initialName, refreshList, scope.cacheKey])

  useEffect(() => {
    if (stateCacheKey !== scope.cacheKey || !selectedName || selectedName === 'new') return
    const token = generation.current.value
    const abort = new AbortController()
    setLoading(true); setError(''); setNotice(''); setIssues([])
    gatewayJson<Response>(`/api/workflows/${enc(selectedName)}`, { signal: abort.signal })
      .then(result => {
        if (abort.signal.aborted || generation.current.value !== token || current.current.selectedName !== selectedName) return
        if (!result.definition) throw new Error('Gideon returned no workflow definition.')
        setDefinition(result.definition); setDraft(structuredClone(result.definition))
        setSelectedKey('root')
      }).catch(cause => {
        if (!abort.signal.aborted && generation.current.value === token && current.current.selectedName === selectedName)
          setError(cause instanceof Error ? cause.message : 'The workflow could not be opened.')
      }).finally(() => {
        if (!abort.signal.aborted && generation.current.value === token && current.current.selectedName === selectedName) setLoading(false)
      })
    return () => abort.abort()
  }, [selectedName, scope.cacheKey, stateCacheKey])

  const nodes = useMemo(() => visibleDraft ? graphOf(visibleDraft.root) : [], [visibleDraft])
  const selected = nodes.find(node => node.key === selectedKey) ?? nodes[0]
  const selectablePorts = nodes.filter(node => node.key !== selected?.key && node.node.id && node.node.id !== selected?.node.id)
  const hasChanges = !!(visibleDefinition && visibleDraft && JSON.stringify(visibleDefinition) !== JSON.stringify(visibleDraft))

  const editSelected = (change: Partial<Pick<WorkflowEditableNode, 'label' | 'prompt'>>) => {
    if (!draft || !selected) return
    setDraft({ ...draft, root: updateNode(draft.root, selected.path, node => ({
      ...node, ...(change.label === undefined ? {} : { label: change.label }),
      ...(change.prompt === undefined ? {} : { config: { ...(node.config ?? {}), prompt: change.prompt } }),
    })) })
  }
  const toggleNeed = (id: string) => {
    if (!draft || !selected) return
    const current = selected.node.needs ?? []
    const needs = current.includes(id) ? current.filter(value => value !== id) : [...current, id]
    setDraft({ ...draft, root: updateNode(draft.root, selected.path, node => ({ ...node, needs })) })
  }
  const addStep = () => {
    if (!draft) return
    const existing = new Set(nodes.map(node => node.node.id).filter(Boolean))
    let index = nodes.length
    while (existing.has(`step-${index}`)) index++
    const node = { kind: 'stage', id: `step-${index}`, label: `Step ${index}`,
      config: { prompt: '' }, needs: [] }
    const children = [...(draft.root.children ?? []), node]
    const root = { ...draft.root, children }
    setDraft({ ...draft, root }); setSelectedKey(`/children/${children.length - 1}`)
  }
  const createWorkflow = () => {
    const name = newName.trim()
    if (!name) { setError('Enter a workflow name first.'); return }
    operation.current++
    setBusy(false); setDefinition(null); setDraft(emptyDefinition(name)); setSelectedName('new'); setSelectedKey('root')
    setError(''); setIssues([]); setNotice('New workflow draft. Add a stage and validate it before saving.')
  }

  const submit = async (action: 'validate' | 'save' | 'publish' | 'start') => {
    if (!ownerMatches || !draft) return
    const token = generation.current.value
    const operationId = ++operation.current
    const snapshot = { cacheKey: scope.cacheKey, selectedName, definition, draft }
    const stillCurrent = () => isCurrent(token, snapshot)
    setBusy(true); setError(''); setNotice(''); setIssues([])
    try {
      if (action === 'validate') {
        const result = await gatewayJson<Response>('/api/workflows', { method: 'POST', body: { ...draft, save: false } })
        if (!stillCurrent()) return
        setIssues(result.issues ?? [])
        setNotice(result.valid ? 'Gideon validated this workflow.' : 'Gideon found issues to resolve.')
        return
      }
      if (action === 'save') {
        if (definition) {
          const current = await gatewayJson<Response>(`/api/workflows/${enc(definition.name)}`)
          if (!stillCurrent()) return
          if (!current.definition || current.definition.version !== definition.version) {
            setError(`Revision conflict: this draft is based on version ${definition.version ?? 'unknown'}, while Gideon now has version ${current.definition?.version ?? 'unavailable'}. Your edits are preserved; reload the current definition before reconciling.`)
            return
          }
        }
        if (!stillCurrent()) return
        const result = await gatewayJson<Response>('/api/workflows', { method: 'POST', body: { ...draft, save: true,
          ...(definition ? { expected_revision: definition.version } : { create_only: true }) } })
        if (!stillCurrent()) return
        setIssues(result.issues ?? [])
        if (result.saved && result.definition) {
          setDefinition(result.definition); setDraft(structuredClone(result.definition)); setSelectedName(result.definition.name)
          setNotice(`Saved ${result.definition.name} as version ${result.definition.version ?? 'current'}.`)
          await refreshList(token)
          if (!definition) onDefinitionSaved?.(result.definition.name)
        } else if (result.valid === false) setNotice('The save was refused because validation found issues; your draft is preserved.')
        else throw new Error('Gideon did not confirm that the workflow was saved.')
        return
      }
      if (action === 'publish') {
        if (!definition) throw new Error('Save this workflow before publishing it.')
        const published = draft.metadata?.a2a_published !== true
        const result = await gatewayJson<Response>(`/api/workflows/${enc(definition.name)}/a2a-publish`, {
          method: 'POST', body: { published, expected_revision: definition.version },
        })
        if (!stillCurrent()) return
        if (result.a2a_published !== published) throw new Error('Gideon did not confirm the publish change.')
        const updated = result.definition ?? { ...draft, metadata: { ...draft.metadata, a2a_published: published } }
        setDefinition(updated); setDraft(structuredClone(updated)); setNotice(published ? 'Workflow published.' : 'Workflow publication withdrawn.')
        return
      }
      const result = await gatewayJson<Response>('/api/workflows/runs', {
        method: 'POST', body: { name: draft.name, mode: 'background' },
      })
      if (!stillCurrent()) return
      if (!result.run_id) throw new Error('Gideon did not return a native workflow run ID.')
      setNotice(`Workflow started · run ${result.run_id}${result.status ? ` · ${result.status}` : ''}.`)
    } catch (cause) {
      if (stillCurrent()) setError(cause instanceof Error ? cause.message : 'The workflow action failed. Your draft is preserved.')
    } finally { if (generation.current.value === token && operation.current === operationId) setBusy(false) }
  }

  const editor = <main aria-label="Workflow definition editor" style={{ width: '100%', minWidth: 0, display: 'grid', gap: 16 }}>
    <header style={{ display: 'grid', gap: 10 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'end' }}>
        <label style={{ display: 'grid', gap: 5, minWidth: 240, flex: '1 1 300px' }}>Workflow definition
          <select aria-label="Workflow definition" value={visibleSelectedName} onChange={event => {
            operation.current++; setBusy(false)
            setSelectedName(event.target.value); setDefinition(null); setDraft(null)
            onDefinitionSelected?.(event.target.value)
          }} style={{ minHeight: 42, font: 'inherit' }}>
            <option value="">Choose a workflow</option>
            {visibleDefinitions.map(row => <option key={row.name} value={row.name}>{row.name} · v{row.version}</option>)}
          </select>
        </label>
        <label style={{ display: 'grid', gap: 5, minWidth: 200 }}>New workflow name
          <input aria-label="New workflow name" value={ownerMatches ? newName : ''} onChange={event => setNewName(event.target.value)} />
        </label>
        <button type="button" style={buttonStyle} disabled={!ownerMatches || busy} onClick={createWorkflow}>New workflow</button>
        <button type="button" style={buttonStyle} disabled={!ownerMatches || busy || loading} onClick={() => void refreshList()}>Refresh</button>
      </div>
      {visibleDraft && <div style={{ display: 'grid', gap: 7, maxWidth: 900 }}>
        <h2 style={{ margin: 0 }}>{visibleDraft.name || 'Untitled workflow'}</h2>
        <label style={{ display: 'grid', gap: 5 }}>Description
          <textarea aria-label="Workflow description" rows={2} value={visibleDraft.description ?? ''}
            onChange={event => setDraft(value => value ? { ...value, description: event.target.value } : value)} />
        </label>
        <p role="status" style={{ margin: 0 }}>Source revision: {visibleDefinition?.version ?? 'new draft'} · {hasChanges ? 'Unsaved edits' : 'In sync'}</p>
      </div>}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" style={buttonStyle} disabled={!visibleDraft || busy} onClick={() => void submit('validate')}>Validate</button>
        <button type="button" style={buttonStyle} disabled={!visibleDraft || busy || (!hasChanges && !!visibleDefinition)} onClick={() => void submit('save')}>Save workflow</button>
        <button type="button" style={buttonStyle} disabled={!visibleDefinition || busy} onClick={() => void submit('publish')}>
          {visibleDraft?.metadata?.a2a_published ? 'Withdraw publication' : 'Publish workflow'}
        </button>
        <button type="button" style={buttonStyle} disabled={!visibleDefinition || busy || hasChanges} onClick={() => void submit('start')}>Start workflow</button>
        {visibleDraft?.root.kind === 'sequence' &&
          <button type="button" style={buttonStyle} disabled={busy} onClick={addStep}>Add stage</button>}
      </div>
    </header>

    {loading && <p role="status">Loading native workflow definitions…</p>}
    {ownerMatches && error && <p role="alert">{error}</p>}
    {ownerMatches && notice && <p role="status">{notice}</p>}
    {ownerMatches && issues.length > 0 && <section aria-label="Validation issues" style={{ display: 'grid', gap: 6 }}>
      <h3 style={{ margin: 0 }}>Validation findings</h3>
      <ul>{issues.map((issue, index) => <li key={`${issue.code ?? 'issue'}:${index}`}>
        {issue.severity ? `${issue.severity}: ` : ''}{issue.message ?? issue.code ?? 'Workflow issue'}{issue.path ? ` · ${issue.path}` : ''}
      </li>)}</ul>
    </section>}
    {visibleDraft && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 16, alignItems: 'start' }}>
      <section aria-label="Workflow graph" style={{ display: 'grid', gap: 9, minWidth: 0 }}>
        <h3 style={{ margin: 0 }}>Workflow graph · {visibleDraft.root.kind}</h3>
        <p style={{ margin: 0 }}>Select a node with the keyboard or pointer. Output ports feed named input dependencies.</p>
        <ol style={{ paddingLeft: 22, display: 'grid', gap: 8 }}>
          {nodes.map(item => {
            const node = item.node
            const outputs = nodes.filter(target => (target.node.needs ?? []).includes(node.id ?? '')).map(target => target.label)
            return <li key={item.key} style={{ minWidth: 0 }}>
              <button type="button" aria-pressed={selected?.key === item.key} style={{ ...buttonStyle, width: '100%', display: 'grid', gap: 4 }}
                onClick={() => setSelectedKey(item.key)}>
                <strong>{item.label}</strong><span>Input port: {(node.needs ?? []).join(', ') || 'start'}</span>
                <span>Output port: {outputs.join(', ') || 'unconnected'}</span>
              </button>
            </li>
          })}
        </ol>
      </section>
      {selected && <WorkflowNodeInspector node={editableNode(selected)}
        choices={selectablePorts.map(item => ({ id: item.node.id!, label: item.label }))}
        onChange={editSelected} onToggleNeed={toggleNeed} />}
    </div>}
    {ownerMatches && !loading && !visibleDraft && !error && <p role="status">Choose a native workflow definition or create a draft.</p>}
  </main>
  return route ? <WorkspaceFrame route={route} mode="full" title="Workflow builder" onBack={onBack}>{editor}</WorkspaceFrame> : editor
}
