import { useMemo, useState } from 'react'
import { ArrowLeft, Check, Save } from 'lucide-react'
import { api, type WorkflowDef, type WorkflowNode } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Segmented } from '../../shared/ui/Segmented'
import { Field, TextInput } from '../../shared/ui/forms'
import { notify } from '../../app/shell/appSdk'
import { editableWorkflow, parseWorkflowJson, replaceStep, workflowJson, workflowSteps, type EditableWorkflow } from './defEditing'
import { WorkflowJsonEditor } from './WorkflowJsonEditor'

export function WorkflowDefEditor({ definition, revision, expectedRevision, copyFrom, restoreVersion, onBack, onSaved }: {
  definition: WorkflowDef
  revision: string
  expectedRevision?: number
  copyFrom?: string
  restoreVersion?: number
  onBack: () => void
  onSaved: (name: string) => void
}) {
  const initial = useMemo(() => editableWorkflow(definition), [definition])
  const [document, setDocument] = useState<EditableWorkflow>(initial)
  const [mode, setMode] = useState<'steps' | 'json'>('steps')
  const [json, setJson] = useState(() => workflowJson(initial))
  const [jsonError, setJsonError] = useState('')
  const [name, setName] = useState(copyFrom ? `${copyFrom}-copy`.slice(0, 63) : definition.name)
  const [busy, setBusy] = useState(false)
  const [issues, setIssues] = useState<string[]>([])
  const [structuredDrafts, setStructuredDrafts] = useState<Record<string, string>>({})
  const isCopy = Boolean(copyFrom)

  const acceptJson = (): EditableWorkflow | null => {
    const parsed = parseWorkflowJson(json)
    if ('error' in parsed) { setJsonError(parsed.error); return null }
    setJsonError('')
    return parsed.document
  }
  const parsedActive = parseWorkflowJson(json)
  const active = mode === 'json' && 'document' in parsedActive ? parsedActive.document : document
  const rows = workflowSteps(active.root)
  const commitStructured = (): EditableWorkflow | null => {
    let next = document
    try {
      for (const [key, source] of Object.entries(structuredDrafts)) {
        const value: unknown = JSON.parse(source)
        if (key === 'inputs') {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Declared inputs must be a JSON object.')
          next = { ...next, inputs: value as WorkflowDef['inputs'] }
        } else if (key === 'settings') {
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Definition settings must be a JSON object.')
          const core = Object.fromEntries(Object.entries(next).filter(([field]) => ['description', 'inputs', 'tags', 'metadata', 'root'].includes(field)))
          next = { ...core, ...(value as Record<string, unknown>) } as EditableWorkflow
        } else if (key.startsWith('step:')) {
          const path = key.slice(5)
          const row = workflowSteps(next.root).find((item) => item.path === path)
          if (!row) throw new Error(`The step at ${path} no longer exists.`)
          next = { ...next, root: replaceStep(next.root, row.hops, value as WorkflowNode) }
        }
      }
    } catch (error) {
      setIssues([error instanceof Error ? error.message : 'A structured field contains invalid JSON.'])
      return null
    }
    setStructuredDrafts({}); setDocument(next); setJson(workflowJson(next)); setIssues([])
    return next
  }
  const check = async (): Promise<EditableWorkflow | null> => {
    const doc = mode === 'json' ? acceptJson() : commitStructured()
    if (!doc) return null
    setBusy(true); setIssues([])
    try {
      const result = await api.saveWorkflowDef({ ...doc, name, save: false, based_on: copyFrom ?? definition.name, ...(restoreVersion ? { based_on_version: restoreVersion } : {}) } as Parameters<typeof api.saveWorkflowDef>[0], isCopy ? undefined : revision)
      if (!result.valid) setIssues((result.issues ?? []).map((issue) => `${issue.path ? `${issue.path}: ` : ''}${issue.message}`))
      else notify('Workflow check passed')
      return result.valid ? doc : null
    } catch (error) {
      setIssues([error instanceof Error ? error.message : 'Workflow check failed'])
      return null
    } finally { setBusy(false) }
  }
  const save = async () => {
    if (busy) return
    const doc = await check()
    if (!doc) return
    setBusy(true)
    try {
      const result = await api.saveWorkflowDef({ ...doc, name, save: true, create_only: isCopy, ...(isCopy ? {} : { expected_revision: expectedRevision ?? definition.version }), based_on: copyFrom ?? definition.name, ...(restoreVersion ? { based_on_version: restoreVersion } : {}) } as Parameters<typeof api.saveWorkflowDef>[0], isCopy ? undefined : revision)
      if (!result.saved) { setIssues((result.issues ?? []).map((issue) => issue.message)); return }
      notify(`Saved ${name}`); onSaved(name)
    } catch (error) {
      setIssues([error instanceof Error ? error.message : 'Could not save this workflow. Your draft is still here.'])
    } finally { setBusy(false) }
  }

  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex items-center justify-between gap-m border-b border-outline px-l py-m">
      <QuietButton onClick={onBack} title="Back to workflow"><ArrowLeft size={14} /> Back</QuietButton>
      <span data-type="title-m" className="truncate text-on-surface">{isCopy ? 'Edit a copy' : restoreVersion ? `Restore v${restoreVersion} as new version` : 'Edit workflow'}</span>
      <div className="flex gap-s"><Button variant="secondary" onClick={check} loading={busy}><Check size={14} /> Check</Button><Button onClick={save} loading={busy}><Save size={14} /> Save</Button></div>
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-l">
      <div className="mx-auto flex max-w-4xl flex-col gap-l">
        <Field label="Workflow name"><TextInput value={name} onChange={setName} ariaLabel="Workflow name" /></Field>
        <Segmented ariaLabel="Editor mode" value={mode} onChange={(value) => {
          if (value === 'steps') { const parsed = acceptJson(); if (!parsed) return; setDocument(parsed); setJson(workflowJson(parsed)) }
          else { const parsed = commitStructured(); if (!parsed) return; setJson(workflowJson(parsed)) }
          setMode(value as 'steps' | 'json')
        }} options={[{ key: 'steps', label: 'Steps' }, { key: 'json', label: 'JSON' }]} />
        {mode === 'json' ? <WorkflowJsonEditor value={json} onChange={setJson} error={jsonError} /> : <>
          <Field label="Description"><textarea aria-label="Description" value={document.description ?? ''} onChange={(event) => { const next = { ...document, description: event.target.value }; setDocument(next); setJson(workflowJson(next)) }} className="min-h-20 w-full rounded-m border border-outline bg-surface px-m py-s text-on-surface" /></Field>
          <Field label="Declared inputs" hint="Edit the full input schema as JSON."><textarea aria-label="Declared inputs" value={structuredDrafts.inputs ?? JSON.stringify(document.inputs ?? {}, null, 2)} onChange={(event) => setStructuredDrafts((drafts) => ({ ...drafts, inputs: event.target.value }))} className="min-h-28 w-full rounded-m border border-outline bg-surface px-m py-s font-mono text-sm text-on-surface" /></Field>
          <div className="flex flex-col gap-m">
            <h2 data-type="title-m" className="text-on-surface">Steps</h2>
            {rows.map(({ path, node, depth }) => <Field key={path} label={`${node.id || node.kind} · ${path}`} hint="Edit all step settings, bindings, and action inputs as JSON.">
              <textarea aria-label={`${node.id || node.kind} settings`} value={structuredDrafts[`step:${path}`] ?? JSON.stringify(node, null, 2)} onChange={(event) => setStructuredDrafts((drafts) => ({ ...drafts, [`step:${path}`]: event.target.value }))} className="min-h-28 w-full rounded-m border border-outline bg-surface px-m py-s font-mono text-sm text-on-surface" style={{ marginLeft: `calc(${depth} * 0.5rem)` }} />
            </Field>)}
          </div>
          <Field label="Additional definition fields" hint="Defaults, overlap policy, runtime hints, metadata, workspace, and other author fields are retained here."><textarea aria-label="Additional definition fields" value={structuredDrafts.settings ?? JSON.stringify(Object.fromEntries(Object.entries(document).filter(([key]) => !['root', 'description', 'inputs', 'tags', 'metadata'].includes(key))), null, 2)} onChange={(event) => setStructuredDrafts((drafts) => ({ ...drafts, settings: event.target.value }))} className="min-h-32 w-full rounded-m border border-outline bg-surface px-m py-s font-mono text-sm text-on-surface" /></Field>
        </>}
        {issues.length > 0 && <div role="alert" className="rounded-m border border-danger/40 bg-danger/10 p-m text-danger"><strong>Check the workflow before saving</strong><ul className="mt-s list-disc pl-l">{issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul></div>}
      </div>
    </div>
  </div>
}
