import { useEffect, useRef, useState } from 'react'
import { api, type PromptItem, type PromptSnippet, type PromptVariable } from '../../shared/data/api'
import { detectIncludes, detectPlaceholders, seedRenderValues } from './promptMeta'
import type { PromptDraft } from './PromptForm'
import type { SnippetDraft } from './SnippetForm'

export function serializeTemplate(draft: PromptDraft | SnippetDraft): Record<string, unknown> {
  const variables: Record<string, unknown>[] = []
  for (const variable of draft.variables) {
    const name = variable.name.trim()
    if (!name) continue
    const entry: Record<string, unknown> = { name, type: variable.type, description: variable.description ?? '', required: Boolean(variable.required) }
    if (variable.default !== undefined && variable.default !== '') entry.default = variable.default
    if (variable.type === 'select') entry.options = variable.options ?? []
    variables.push(entry)
  }
  const payload: Record<string, unknown> = { content: draft.content, tags: [...draft.tags], variables }
  for (const field of ['name', 'title', 'description'] as const) payload[field] = draft[field].trim()
  if ('kind' in draft) { payload.kind = draft.kind; payload.launch_spec = draft.launchSpec ?? {} }
  return payload
}
export function templateDraft(record: PromptItem | PromptSnippet): SnippetDraft {
  return { name: record.name, title: record.title ?? '', description: record.description ?? '', content: record.content ?? '', tags: [...(record.tags ?? [])], variables: (record.variables ?? []).map(variable => ({ ...variable, ...(variable.options ? { options: [...variable.options] } : {}) })), source: record.source }
}
export function useTemplateEditing<Draft extends SnippetDraft>(draft: Draft, onChange: (next: Draft) => void, registerInsert?: (insert: (text: string) => void) => void) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const latest = useRef({ draft, onChange })
  latest.current = { draft, onChange }
  const frame = useRef<number | null>(null)
  const set = <Key extends keyof Draft,>(key: Key, value: Draft[Key]) => onChange({ ...draft, [key]: value })
  const insertAtCursor = (text: string) => {
    const { draft: current, onChange: publish } = latest.current
    const element = taRef.current
    const start = element?.selectionStart ?? current.content.length
    const end = element?.selectionEnd ?? start
    publish({ ...current, content: current.content.substring(0, start) + text + current.content.substring(end) })
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => { frame.current = null; if (element?.isConnected) { element.focus(); element.setSelectionRange(start + text.length, start + text.length) } })
  }
  useEffect(() => { registerInsert?.(insertAtCursor) }, [registerInsert])
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current) }, [])
  const declared = new Set(draft.variables.map(variable => variable.name))
  const addVars = (names: string[]) => set('variables', [...draft.variables, ...names.map(name => ({ name, type: 'text' as const, description: '', required: false }))] as Draft['variables'])
  return { set, taRef, insertAtCursor, includes: detectIncludes(draft.content), undeclared: detectPlaceholders(draft.content).filter(name => !declared.has(name)), addVars, addVar: (name = '') => addVars([name]), updateVar: (index: number, patch: Partial<PromptVariable>) => set('variables', draft.variables.map((variable, at) => at === index ? { ...variable, ...patch } : variable) as Draft['variables']), removeVar: (index: number) => set('variables', draft.variables.filter((_, at) => at !== index) as Draft['variables']) }
}
export function usePromptRequest(identity: string) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = useRef<object | null>(null)
  useEffect(() => { active.current = null; setBusy(false); setError(''); return () => { active.current = null } }, [identity])
  const run = async <Value,>(request: () => Promise<Value>, accept: (value: Value) => void, fallback: string) => {
    if (active.current) return
    const ticket = {}; active.current = ticket; setBusy(true); setError('')
    try { const value = await request(); if (active.current === ticket) accept(value) }
    catch (failure) { if (active.current === ticket) setError(failure instanceof Error ? failure.message : fallback) }
    finally { if (active.current === ticket) { active.current = null; setBusy(false) } }
  }
  return { busy, error, setError, run }
}
export function useTemplatePreview(draft: PromptDraft) {
  const vars = draft.variables.filter(variable => variable.name.trim())
  const signature = JSON.stringify(vars)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [state, setState] = useState({ rendered: '', error: null as string | null, includes: [] as string[], busy: false })
  useEffect(() => { setValues(previous => ({ ...seedRenderValues(vars), ...previous })) }, [signature])
  const sampleKey = JSON.stringify(values)
  useEffect(() => {
    let current = true
    if (!draft.content.trim()) { setState({ rendered: '', error: null, includes: [], busy: false }); return }
    setState(previous => ({ ...previous, busy: true }))
    const timer = setTimeout(() => {
      api.previewPrompt({ content: draft.content, variables: vars, values }).then(result => {
        if (!current) return
        setState(previous => ({ rendered: result.ok ? result.rendered ?? '' : previous.rendered, error: result.ok ? null : result.error ?? 'Could not render', includes: result.includes ?? [], busy: false }))
      }).catch(failure => { if (current) setState(previous => ({ ...previous, error: failure instanceof Error ? failure.message : 'Could not render', busy: false })) })
    }, 250)
    return () => { current = false; clearTimeout(timer) }
  }, [draft.content, signature, sampleKey])
  return { ...state, vars, values, setVal: (name: string, value: unknown) => setValues(previous => ({ ...previous, [name]: value })) }
}
export function useTemplateRender(name: string, vars: PromptVariable[], snippet = false) {
  const [values, setValues] = useState<Record<string, unknown>>(() => seedRenderValues(vars))
  const [out, setOut] = useState<string | null>(null)
  const request = usePromptRequest(name)
  const launchRequest = usePromptRequest(name)
  useEffect(() => { setValues(seedRenderValues(vars)); setOut(null) }, [name])
  const render = () => { launchRequest.setError(''); setOut(null); void request.run(() => snippet ? api.renderSnippet(name, values) : api.renderPrompt(name, values), result => setOut(result.rendered), 'Render failed') }
  const launch = (navigate: (path: string) => void) => {
    request.setError('')
    const missing = vars.filter(variable => variable.required && (values[variable.name] == null || String(values[variable.name]).trim() === '')).map(variable => variable.name)
    if (missing.length) { launchRequest.setError(`Fill the required variable${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}`); return }
    void launchRequest.run(() => api.launchCampaignTemplate(name, values), result => navigate(`${result.kind === 'code' ? 'code' : 'loops'}/${result.loop_id}`), 'Launch failed')
  }
  return { values, setValues, out, err: launchRequest.error || request.error, loading: request.busy, launching: launchRequest.busy, render, launch }
}
