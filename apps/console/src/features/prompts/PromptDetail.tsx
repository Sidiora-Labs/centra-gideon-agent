import { usePromptRequest, useTemplateRender } from './promptEditorState'
import { useEffect, useState } from 'react'
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
import { Field, FieldError } from '../../shared/ui/forms'
import { isReadOnly, sourceTone, sourceLabel, promptVars, mergePromptVariables, variableTypeLabel } from './promptMeta'
import { toDraft, draftToPayload, type PromptDraft } from './PromptForm'
import { PromptEditFields } from './PromptEditFields'
import { accentChip } from '../../shared/theme/accent'

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
  const request = usePromptRequest(prompt.name)
  const { busy: saving, error: err, setError: setErr } = request
  const { data: fetched, refresh: refetch } = useQuery<PromptItem | undefined>(`prompt:${prompt.name}`, () => (prompt.content == null ? api.prompt(prompt.name) : Promise.resolve(undefined)), { persist: true })
  const full = prompt.content != null ? prompt : fetched

  useEffect(() => { if (full) setDraft(toDraft(full)) }, [full])

  const save = () => {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    void request.run(() => api.savePrompt(prompt.name, draftToPayload(draft)), result => { invalidateKeys(`prompt:${prompt.name}`); refetch(); onSaved(result.prompt?.name ?? prompt.name); setEditing(false) }, 'Save failed')
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
        <PromptEditFields draft={draft} onChange={setDraft} Section={Section} />
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={() => { if (full) setDraft(toDraft(full)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving} disabled={saving || !draft.name.trim()}
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
    case 'select': return <select {...common} aria-label={label} value={text} onChange={event => onChange(event.target.value)}><option value="">—</option>{(v.options ?? []).map(option => <option key={option} value={option}>{option}</option>)}</select>
    case 'textarea': return <textarea {...common} aria-label={label} value={text} onChange={event => onChange(event.target.value)} rows={3} placeholder={v.description} />
    case 'number': return <input {...common} aria-label={label} type="number" value={value === '' || value == null ? '' : Number(value)} onChange={event => onChange(event.target.value === '' ? '' : Number(event.target.value))} placeholder={v.description} />
    default: return <input {...common} aria-label={label} value={text} onChange={event => onChange(event.target.value)} placeholder={v.description} />
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
