import { usePromptRequest, useTemplateRender } from './promptEditorState'
import { useEffect, useRef, useState } from 'react'
import { toneChipSkin } from '../../shared/theme/accent'
import { Pencil, Trash2, Check, X, Play, Lock, Code2, Eye, Puzzle, Rocket } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FormFooter } from '../../shared/ui/FormFooter'
import { Toggle } from '../../shared/ui/Toggle'
import { Markdown } from '../../shared/ui/Markdown'
import { Skeleton } from '../../shared/ui/ListScaffold'
import { confirmDelete } from '../../shared/ui/dialog'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type PromptItem, type PromptVariable } from '../../shared/data/api'
import { useStaleWriteGuard } from '../../shared/data/useStaleWriteGuard'
import { HeldChange, StaleWriteNotice } from '../../shared/ui/StaleWriteNotice'
import { Field, FieldError, TextArea, TextInput, Select } from '../../shared/ui/forms'
import { isReadOnly, sourceTone, sourceLabel, promptVars, mergePromptVariables, variableTypeLabel } from './promptMeta'
import { toDraft, draftToPayload, type PromptDraft } from './PromptForm'
import { PromptEditFields } from './PromptEditFields'
import { accentChip } from '../../shared/theme/accent'

type PromptDraftEntry = { draft: PromptDraft; base: PromptDraft; revision?: string }
const promptDrafts = new Map<string, PromptDraftEntry>()
const promptDraftEqual = (a: PromptDraft, b: PromptDraft) => JSON.stringify(a) === JSON.stringify(b)
function retainPromptDraft(name: string, draft: PromptDraft, base: PromptDraft, revision?: string) {
  if (promptDraftEqual(draft, base)) promptDrafts.delete(name)
  else promptDrafts.set(name, { draft, base, revision })
}
function mergePromptDraft(base: PromptDraft, mine: PromptDraft, theirs: PromptDraft): PromptDraft | null {
  const merged = { ...base } as PromptDraft
  for (const key of Object.keys(base) as (keyof PromptDraft)[]) {
    const b = base[key], m = mine[key], t = theirs[key]
    const fields = merged as unknown as Record<keyof PromptDraft, unknown>
    if (JSON.stringify(m) === JSON.stringify(b)) fields[key] = t
    else if (JSON.stringify(t) === JSON.stringify(b) || JSON.stringify(m) === JSON.stringify(t)) fields[key] = m
    else return null
  }
  return merged
}

function fillDefaults(content: string, vars: PromptVariable[]): string {
  return vars.reduce((text, variable) => variable.default == null || variable.default === '' ? text : text.replaceAll(`{{${variable.name}}}`, () => String(variable.default)), content)
}

export function PromptDetail({ prompt, onSaved, onDeleted, editing: editingProp, onEditingChange, onNavigate, deletionBlockedReason }: {
  prompt: PromptItem
  onSaved: (name: string) => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
  onNavigate: (path: string) => void
  deletionBlockedReason?: string
}) {
  const readOnly = isReadOnly(prompt.source)
  const editing = editingProp && !readOnly
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<PromptDraft>(() => toDraft(prompt))
  const [baseDraft, setBaseDraft] = useState<PromptDraft>(() => toDraft(prompt))
  const [draftName, setDraftName] = useState(prompt.name)
  const [revision, setRevision] = useState(prompt.revision ?? '')
  const request = usePromptRequest(prompt.name)
  const { busy: saving, error: err, setError: setErr } = request
  const { data: fetched, refresh: refetch } = useQuery<PromptItem | undefined>(`prompt:${prompt.name}`, () => (prompt.content == null ? api.prompt(prompt.name) : Promise.resolve(undefined)), { persist: true })
  const full = prompt.content != null ? prompt : fetched
  const authority = useRef<{ draft: PromptDraft; revision: string } | null>(null)
  const savedAuthority = useRef<{ draft: PromptDraft; revision: string } | null>(null)
  const stale = useStaleWriteGuard<PromptDraft>({
    read: async () => {
      const current = await api.prompt(prompt.name)
      if (!current.revision) throw new Error('The current prompt has no revision.')
      const value = { draft: toDraft(current), revision: current.revision }
      authority.current = value
      return { value: value.draft, revision: value.revision }
    },
    write: async (next, basedOn) => {
      await api.savePrompt(prompt.name, draftToPayload(next), basedOn)
      const current = await api.prompt(prompt.name)
      if (!current.revision) throw new Error('The saved prompt has no revision.')
      const value = { draft: toDraft(current), revision: current.revision }
      authority.current = value; savedAuthority.current = value
    },
    onSaved: () => {
      const current = savedAuthority.current
      if (!current) return
      setDraft(current.draft); setBaseDraft(current.draft); setRevision(current.revision)
      retainPromptDraft(prompt.name, current.draft, current.draft, current.revision)
    },
    onDiscard: () => {
      const current = authority.current
      if (!current) return
      setDraft(current.draft); setBaseDraft(current.draft); setRevision(current.revision)
      retainPromptDraft(prompt.name, current.draft, current.draft, current.revision)
    },
  })
  useEffect(() => {
    if (!full) return
    const cached = promptDrafts.get(prompt.name)
    const current = toDraft(full)
    const preserve = cached && !promptDraftEqual(cached.draft, cached.base) && !promptDraftEqual(cached.draft, current) ? cached : undefined
    const next = preserve ?? { draft: current, base: current, revision: full.revision }
    if (cached && !preserve) retainPromptDraft(prompt.name, current, current, full.revision)
    setDraftName(prompt.name)
    authority.current = { draft: current, revision: full.revision ?? '' }
    setDraft(next.draft); setBaseDraft(next.base); setRevision(next.revision ?? '')
  }, [prompt.name, full])
  useEffect(() => { if (draftName === prompt.name) retainPromptDraft(prompt.name, draft, baseDraft, revision || undefined) }, [draftName, prompt.name, draft, baseDraft, revision])
  const baseMissing = !revision && !promptDraftEqual(draft, baseDraft)
  const rebaseMissing = async () => {
    setErr('')
    try {
      const current = await api.prompt(prompt.name)
      if (!current.revision) throw new Error('The current prompt has no revision. Reload it before saving.')
      const merged = mergePromptDraft(baseDraft, draft, toDraft(current))
      if (!merged) throw new Error('The edits overlap. Keep this draft and resolve the difference before rebasing.')
      const value = { draft: toDraft(current), revision: current.revision }
      authority.current = value
      setDraft(merged); setBaseDraft(value.draft); setRevision(value.revision)
      retainPromptDraft(prompt.name, merged, value.draft, value.revision)
    } catch (error) { setErr(error instanceof Error ? error.message : 'Could not rebase this prompt draft.') }
  }
  const save = () => {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    if (!revision) { setErr('This saved draft has no matching revision. Refresh and rebase it before saving.'); return }
    void request.run(async () => stale.save({ value: baseDraft, revision }, draft, theirs => mergePromptDraft(baseDraft, draft, theirs)), didSave => {
      if (!didSave) return
      invalidateKeys(`prompt:${prompt.name}`); refetch()
      onSaved(savedAuthority.current?.draft.name ?? prompt.name); setEditing(false)
    }, 'Save failed')
  }
  const discardDraft = () => {
    void request.run(async () => {
      const current = await api.prompt(prompt.name)
      if (!current.revision) throw new Error('The current prompt has no revision. Reload it before discarding this draft.')
      return current
    }, current => {
      const value = toDraft(current)
      authority.current = { draft: value, revision: current.revision! }
      setDraft(value); setBaseDraft(value); setRevision(current.revision!)
      retainPromptDraft(prompt.name, value, value, current.revision)
      setEditing(false); setErr('')
    }, 'Could not refresh the prompt')
  }
  const del = async () => {
    if (readOnly || deletionBlockedReason || !await confirmDelete('prompt', prompt.name)) return
    await request.run(() => api.deletePrompt(prompt.name), onDeleted, 'Delete failed')
  }

  if (editing) {
    return (
      <div className="grid gap-l">
        <div className="flex items-center gap-s">
          <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Pencil size={13} /> Editing</span>

          <span data-type="caption" className="ml-auto inline-flex items-center rounded-md px-m h-6" style={toneChipSkin(sourceTone(prompt.source), 16)}>{sourceLabel(prompt.source, full?.tags)}</span>
        </div>
        {err && <FieldError>{err}</FieldError>}
        <StaleWriteNotice guard={stale} what="This prompt" />
        {baseMissing && <div role="alert" className="flex items-center gap-2 text-xs text-warning"><span>This saved draft has no matching revision.</span><Button size="sm" variant="secondary" onClick={() => void rebaseMissing()}>Refresh and rebase draft</Button></div>}
        <HeldChange guard={stale}><PromptEditFields draft={draft} onChange={setDraft} Section={Section} /></HeldChange>
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={discardDraft} loading={saving} disabled={saving}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving} disabled={saving || !draft.name.trim() || baseMissing || stale.conflict !== null}
            disabledReason={!draft.name.trim() ? 'Enter a name first' : undefined}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  if (full === undefined) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }
  const ownVars = promptVars(full)
  const vars = mergePromptVariables(ownVars, full.merged_variables)
  const includes = full.includes ?? []
  return (
    <div className="grid gap-l">
      <div className="flex items-center gap-s">
        {readOnly ? (
          <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Lock size={13} /> {sourceLabel(prompt.source)} — read-only</span>
        ) : (
          <>
            <Button size="sm" variant="secondary" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
            <Button size="sm" variant="ghost" onClick={del} disabled={!!deletionBlockedReason} disabledReason={deletionBlockedReason}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        {full.kind && <span data-type="caption" className="inline-flex items-center rounded-md px-m h-6" style={{ background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>{full.kind} prompt</span>}
        {full.launch_spec && Object.keys(full.launch_spec).length > 0 && (
          <span data-type="caption" className="inline-flex items-center gap-xs rounded-md px-m h-6" style={accentChip}><Rocket size={11} /> runnable</span>
        )}
        <span data-type="caption" className="ml-auto inline-flex items-center rounded-md px-m h-6" style={toneChipSkin(sourceTone(prompt.source), 16)}>{sourceLabel(prompt.source, full?.tags)}</span>
      </div>
      {err && <FieldError>{err}</FieldError>}

      {full.title && <h2 data-type="title-m" className="text-on-surface">{full.title}</h2>}
      {full.description && <p data-type="body-m" className="text-on-surface leading-relaxed">{full.description}</p>}

      {(full.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">{full.tags!.map((t) => <span key={t} data-type="caption" className="rounded-md bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var">{t}</span>)}</div>
      )}

      {includes.length > 0 && (
        <Section label={`Includes · ${includes.length}`}>
          <div className="flex flex-wrap gap-1.5">
            {includes.map((n) => (
              <span key={n} data-type="caption" className="inline-flex items-center gap-xs rounded-md border border-outline-variant/25 bg-surface-container/40 px-2 h-7 font-mono text-on-surface-var"><Puzzle size={12} className="text-info" /> {n}</span>
            ))}
          </div>
        </Section>
      )}

      {vars.length > 0 && (
        <Section label={`Variables · ${vars.length}`}>
          <div className="flex flex-col gap-1.5">
            {vars.map((v) => (
              <div key={v.name} className="rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-1.5">
                <div className="flex items-center gap-s">
                  <span data-type="body-s" className="font-mono text-on-surface">{v.name}</span>
                  <span data-type="caption" className="text-on-surface-low">{variableTypeLabel(v.type)}</span>
                  {(v.options?.length ?? 0) > 0 && <span data-type="caption" className="text-on-surface-low">{v.options!.join(' · ')}</span>}
                  {v.required && <span data-type="caption" className="text-danger">required</span>}
                  {v.default != null && v.default !== '' && <span data-type="caption" className="text-on-surface-low">default: {String(v.default)}</span>}
                </div>
                {v.description && <p data-type="body-s" className="mt-0.5 text-on-surface-var">{v.description}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      <TemplateSection content={full.content || ''} vars={vars} />

      <RenderPanel name={prompt.name} vars={vars} onNavigate={onNavigate}
        launchable={!!full.launch_spec && Object.keys(full.launch_spec).length > 0}
        launchKind={full.launch_spec?.kind ?? 'goal'} />
    </div>
  )
}

function RenderPanel({ name, vars, launchable, launchKind, onNavigate }: { name: string; vars: PromptVariable[]; launchable?: boolean; launchKind?: string; onNavigate: (path: string) => void }) {
  const { values, setValues, out, err, loading, render, launching, launch: launchRun } = useTemplateRender(name, vars)
  const launch = () => launchRun(onNavigate)

  return (
    <Section label={launchable ? 'Fill & launch' : 'Try it'}>
      <div className="flex flex-col gap-2">
        {vars.map((v) => (
          <Field key={v.name} label={`${v.name}${v.required ? ' *' : ''}`}>
            <RenderInput v={v} value={values[v.name]} onChange={(val) => setValues((s) => ({ ...s, [v.name]: val }))} />
          </Field>
        ))}
        <div className="flex items-center gap-2">
          <Button size="sm" variant={launchable ? 'secondary' : 'primary'} onClick={render} loading={loading} className="self-start"><Play size={15} /> {launchable ? 'Preview' : 'Render'}</Button>
          {launchable && (
            <Button size="sm" onClick={launch} loading={launching} className="self-start"><Rocket size={15} /> {launching ? 'Launching…' : `Launch ${launchKind ?? 'goal'} run`}
            </Button>
          )}
        </div>
      </div>
      {err && <FieldError className="mt-2">{err}</FieldError>}
      {out != null && (
        <div data-type="body-s" className="mt-2 rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-on-surface-var leading-relaxed"><Markdown>{out}</Markdown></div>
      )}
    </Section>
  )
}

function RenderInput({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (value: unknown) => void }) {
  const base = 'w-full rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  const fid = `prompt-var-${v.name}`
  const label = `${v.name} value`
  const text = String(value ?? '')
  const common = { id: fid, name: v.name, 'data-type': 'body-s', className: base }
  switch (v.type) {
    case 'boolean': return <Toggle on={Boolean(value)} onChange={onChange} size="sm" />
    case 'select': return <Select {...common} ariaLabel={label} value={text} onChange={onChange} size="sm" options={[{ value: '', label: '—' }, ...(v.options ?? []).map(option => ({ value: option, label: option }))]} />
    case 'textarea': return <TextArea {...common} ariaLabel={label} value={text} onChange={nextValue => onChange(nextValue)} rows={3} placeholder={v.description} size="sm" surface="container" />
    case 'number': return <TextInput {...common} ariaLabel={label} type="number" value={value === '' || value == null ? '' : String(Number(value))} onChange={nextValue => onChange(nextValue === '' ? '' : Number(nextValue))} placeholder={v.description} size="sm" surface="container" />
    default: return <TextInput {...common} ariaLabel={label} value={text} onChange={nextValue => onChange(nextValue)} placeholder={v.description} size="sm" surface="container" />
  }
}

function TemplateSection({ content, vars }: { content: string; vars: PromptVariable[] }) {
  const [mode, setMode] = useState<'rendered' | 'raw'>('rendered')
  const raw = mode === 'raw'
  const title = raw ? 'Show rendered' : 'Show raw template'
  const body = raw ? <pre data-type="body-s" className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-on-surface-var">{content || '—'}</pre> : <Markdown>{fillDefaults(content, vars) || '—'}</Markdown>
  return <section className="grid gap-s"><div className="flex items-center gap-s"><h2 data-type="caption" className="text-on-surface-low uppercase tracking-wide">Template</h2><button type="button" onClick={() => setMode(raw ? 'rendered' : 'raw')} data-type="caption" className="ml-auto inline-flex min-h-6 items-center gap-xs rounded-md border border-outline-variant/30 px-s text-on-surface-low hover:text-on-surface" title={title}>{raw ? <><Eye size={12} /> Rendered</> : <><Code2 size={12} /> Raw</>}</button></div><div data-type="body-s" className="rounded-md border border-outline-variant/25 bg-surface-container/30 p-m leading-relaxed text-on-surface">{body}</div></section>
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="grid gap-s"><h2 data-type="caption" className="border-l-2 border-primary/40 pl-s text-on-surface-low uppercase tracking-wide">{label}</h2>{children}</section>
}
