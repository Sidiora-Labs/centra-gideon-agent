import { usePromptRequest, useTemplateRender } from './promptEditorState'
import { useEffect, useRef, useState } from 'react'
import { toneChipSkin } from '../../shared/theme/accent'
import { Pencil, Trash2, Check, X, Play, Loader2, Lock } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FormFooter } from '../../shared/ui/FormFooter'
import { Markdown } from '../../shared/ui/Markdown'
import { Field, FieldError } from '../../shared/ui/forms'
import { confirmDelete } from '../../shared/ui/dialog'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { api, type PromptSnippet, type PromptVariable } from '../../shared/data/api'
import { mergeRecord } from '../../shared/data/staleWrite'
import { useStaleWriteGuard } from '../../shared/data/useStaleWriteGuard'
import { HeldChange, StaleWriteNotice } from '../../shared/ui/StaleWriteNotice'
import { isReadOnly, sourceTone, sourceLabel, promptVars, detectIncludes } from './promptMeta'
import { SnippetForm, toSnippetDraft, snippetDraftToPayload, type SnippetDraft } from './SnippetForm'

export function SnippetDetail({ snippet, onSaved, onDeleted, editing: editingProp, onEditingChange }: {
  snippet: PromptSnippet
  onSaved: (name: string) => void
  onDeleted: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  const readOnly = isReadOnly(snippet.source)
  const editing = editingProp && !readOnly
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<SnippetDraft>(() => toSnippetDraft(snippet))
  const request = usePromptRequest(snippet.name)
  const { busy: saving, error: err, setError: setErr } = request

  const { data: fetched, refresh: refetch } = useQuery<PromptSnippet | undefined>(`snippet:${snippet.name}`, () => (snippet.content == null ? api.snippet(snippet.name) : Promise.resolve(undefined)), { persist: true })
  const full = snippet.content != null ? snippet : fetched
  const latestRead = useRef(full ?? snippet)
  const writeResult = useRef<PromptSnippet | null>(null)
  const [base, setBase] = useState({ value: toSnippetDraft(full ?? snippet), revision: (full ?? snippet).revision ?? '' })
  const stale = useStaleWriteGuard<SnippetDraft>({
    read: async () => {
      const current = await api.snippet(snippet.name)
      if (!current.revision) throw new Error('The snippet has no current revision.')
      latestRead.current = current
      writeResult.current = null
      return { value: toSnippetDraft(current), revision: current.revision }
    },
    write: async (next, basedOn) => {
      const result = await api.saveSnippet(snippet.name, snippetDraftToPayload(next), basedOn)
      writeResult.current = result.snippet
      latestRead.current = result.snippet
    },
    onSaved: (_saved) => {
      const current = writeResult.current ?? latestRead.current
      setBase({ value: toSnippetDraft(current), revision: current.revision ?? '' })
      setDraft(toSnippetDraft(current))
      invalidateKeys(`snippet:${snippet.name}`)
      refetch()
      onSaved(current.name ?? snippet.name)
      setEditing(false)
    },
    onDiscard: () => {
      const current = latestRead.current
      setBase({ value: toSnippetDraft(current), revision: current.revision ?? '' })
      setDraft(toSnippetDraft(current))
    },
  })

  useEffect(() => {
    if (!full || editingProp || stale.conflict) return
    latestRead.current = full
    setBase({ value: toSnippetDraft(full), revision: full.revision ?? '' })
    setDraft(toSnippetDraft(full))
  }, [full])

  const save = () => {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    if (!base.revision) { setErr('Reload this snippet before saving.'); return }
    setErr('')
    void request.run(() => stale.save(base, draft, (theirs) => mergeRecord(base.value, draft, theirs)), () => {}, 'Save failed')
  }
  const del = async () => {
    if (readOnly || !await confirmDelete('snippet', snippet.name)) return
    await request.run(() => api.deleteSnippet(snippet.name), onDeleted, 'Delete failed')
  }

  if (editing) {
    return (
      <div className="grid gap-l">
        <div className="flex items-center gap-s">
          <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Pencil size={13} /> Editing</span>
        </div>
        <StaleWriteNotice guard={stale} what="This snippet" />
        {err && <FieldError>{err}</FieldError>}
        <HeldChange guard={stale}><SnippetForm draft={draft} onChange={setDraft} nameLocked /></HeldChange>
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={() => { stale.discard(); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving || stale.busy} disabled={saving || stale.busy || stale.conflict !== null || !draft.name.trim()}
            disabledReason={!draft.name.trim() ? 'Enter a name first' : undefined}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  if (full === undefined) {
    return <div className="flex h-40 items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
  }

  const vars = promptVars(full)
  const includes = detectIncludes(full.content || '')
  const usedBy = [...(full.used_by?.prompts ?? []), ...(full.used_by?.snippets ?? [])]
  return (
    <div className="grid gap-l">
      <div className="flex items-center gap-s">
        {readOnly ? (
          <span data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface-low"><Lock size={13} /> {sourceLabel(snippet.source)} — read-only</span>
        ) : (
          <>
          <Button size="sm" variant="secondary" onClick={() => { const current = full ?? snippet; latestRead.current = current; writeResult.current = null; setBase({ value: toSnippetDraft(current), revision: current.revision ?? '' }); setDraft(toSnippetDraft(current)); setEditing(true) }}><Pencil size={14} /> Edit</Button>
            <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
          </>
        )}
        <span data-type="caption" className="ml-auto inline-flex items-center rounded-md px-m h-6" style={toneChipSkin(sourceTone(snippet.source), 16)}>{sourceLabel(snippet.source, full?.tags)}</span>
      </div>
      {err && <FieldError>{err}</FieldError>}

      {full.title && <h2 data-type="title-m" className="text-on-surface">{full.title}</h2>}
      {full.description && <p data-type="body-m" className="text-on-surface leading-relaxed">{full.description}</p>}

      {(full.tags?.length ?? 0) > 0 && (
        <div className="flex flex-wrap gap-1.5">{full.tags!.map((t) => <span key={t} data-type="caption" className="rounded-md bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var">{t}</span>)}</div>
      )}

      {usedBy.length > 0 && (
        <Section label={`Used by · ${usedBy.length}`}>
          <p data-type="caption" className="mb-1 text-on-surface-low">Prompts/snippets that include this — deleting it would break them.</p>
          <div className="flex flex-wrap gap-1.5">{usedBy.map((n) => <span key={n} data-type="caption" className="rounded-md border border-outline-variant/25 bg-surface-container/40 px-2 h-7 inline-flex items-center font-mono text-on-surface-var">{n}</span>)}</div>
        </Section>
      )}

      {includes.length > 0 && (
        <Section label={`Includes · ${includes.length}`}>
          <div className="flex flex-wrap gap-1.5">{includes.map((n) => <span key={n} data-type="caption" className="rounded-md border border-outline-variant/25 bg-surface-container/40 px-2 h-7 inline-flex items-center font-mono text-on-surface-var">{n}</span>)}</div>
        </Section>
      )}

      {vars.length > 0 && (
        <Section label={`Variables · ${vars.length}`}>
          <div className="flex flex-col gap-1.5">
            {vars.map((v) => (
              <div key={v.name} className="rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-1.5">
                <div className="flex items-center gap-s">
                  <span data-type="body-s" className="font-mono text-on-surface">{v.name}</span>
                  <span data-type="caption" className="text-on-surface-low">{v.type}</span>
                  {v.required && <span data-type="caption" className="text-danger">required</span>}
                </div>
                {v.description && <p data-type="body-s" className="mt-0.5 text-on-surface-var">{v.description}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      <div>
        <div data-type="caption" className="text-on-surface-low uppercase tracking-wide mb-1.5">Content</div>
        <pre data-type="body-s" className="rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-on-surface-var font-mono overflow-x-auto whitespace-pre-wrap break-words">{full.content || '—'}</pre>
      </div>

      <SnippetRenderPanel name={snippet.name} vars={vars} />
    </div>
  )
}

function SnippetRenderPanel({ name, vars }: { name: string; vars: PromptVariable[] }) {
  const { values, setValues, out, err, loading, render } = useTemplateRender(name, vars, true)

  return (
    <Section label="Try it">
      <div className="flex flex-col gap-2">
        {vars.map((v) => (
          <Field key={v.name} label={`${v.name}${v.required ? ' *' : ''}`}>

            <input value={String(values[v.name] ?? '')} aria-label={`${v.name} value`}
              onChange={(e) => setValues((s) => ({ ...s, [v.name]: e.target.value }))} placeholder={v.description}
              data-type="body-s"
              className="w-full rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          </Field>
        ))}
        <Button size="sm" onClick={render} loading={loading} className="self-start"><Play size={15} /> Render</Button>
      </div>
      {err && <FieldError className="mt-2">{err}</FieldError>}
      {out != null && (
        <div data-type="body-s" className="mt-2 rounded-md border border-outline-variant/25 bg-surface-container/40 px-m py-s text-on-surface-var leading-relaxed"><Markdown>{out}</Markdown></div>
      )}
    </Section>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <section className="grid gap-s"><h2 data-type="caption" className="border-l-2 border-primary/40 pl-s text-on-surface-low uppercase tracking-wide">{label}</h2>{children}</section>
}
