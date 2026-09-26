import { LoadError } from '../../shared/ui/ListScaffold'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { SearchField } from '../../shared/ui/SearchField'
import { FieldError } from '../../shared/ui/forms'
import { Loader2, Search, ChevronLeft, FileText, CornerDownLeft } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { api, type PromptItem, type PromptVariable } from '../../shared/data/api'
import { seedRenderValues } from '../prompts/promptMeta'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { PromptLibrary } from '../../shared/vendor/assistant-ui/elements/prompt-library'

export function PromptPalette({ onInsert, onSend, onClose }: {
  onInsert: (text: string) => void
  onSend?: (text: string) => void
  onClose: () => void
}) {
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [items, setItems] = useState<PromptItem[] | null>(null)
  const [q, setQ] = useState('')
  const [selectedName, setSelectedName] = useState('')
  const [picked, setPicked] = useState<PromptItem | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const picking = useRef(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    setLoadErr(null)
    api.prompts('user').then((items) => { if (live) setItems(items) }).catch((error) => { if (live) setLoadErr(error) })
    return () => { live = false }
  }, [loadAttempt])

  const filtered = useMemo(() => {
    if (!items) return null
    const n = q.trim().toLowerCase()
    return n ? items.filter((p) => `${p.name} ${p.title ?? ''} ${p.description ?? ''} ${(p.tags ?? []).join(' ')}`.toLowerCase().includes(n)) : items
  }, [items, q])

  async function pick(p: PromptItem) {
    if (picking.current) return
    picking.current = true
    setErr(''); setLoadingDetail(true)
    try {
      const full = await api.prompt(p.name)
      const vars = full.merged_variables?.length ? full.merged_variables : (full.variables ?? [])
      if (vars.length === 0) {
        const r = await api.renderPrompt(p.name, {})
        onInsert(r.rendered)
        onClose()
        return
      }
      setPicked(full)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load that prompt')
    } finally { picking.current = false; setLoadingDetail(false) }
  }

  return (
    <Modal title={picked ? (picked.title || picked.name) : 'Insert a prompt'}
      icon={picked ? <FileText size={18} className="text-primary" /> : <Search size={18} className="text-primary" />}
      onClose={onClose}>
      {picked ? (
        <FillIn prompt={picked} onBack={() => setPicked(null)} onInsert={(t) => { onInsert(t); onClose() }} onSend={onSend ? (t) => { onSend(t); onClose() } : undefined} />
      ) : (
        <div className="flex min-h-[320px] flex-col gap-3">
          <SearchField variant="inline" size="md" clearable={false} autoFocus value={q} onChange={setQ}
            placeholder="Search your prompts…" ariaLabel="Search prompts"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && filtered && filtered.length > 0 && !loadingDetail) { e.preventDefault(); void pick(filtered[0]) }
              else if (e.key === 'Escape' && q) { e.preventDefault(); setQ('') }
            }} />
          {loadingDetail && <div role="status" className="flex items-center gap-2 px-1 text-sm text-on-surface-low"><Loader2 size={14} className="animate-spin" /> Loading prompt…</div>}
          <ResultAnnouncement count={filtered?.length ?? 0} noun="prompts" active={!!q.trim() && filtered !== null} />
          {!!filtered?.length && (
            <div data-type="caption" className="flex items-center gap-3 px-1 text-on-surface-low">
              <span className="inline-flex items-center gap-1"><CornerDownLeft size={11} /> picks the first match</span>
              {!!q.trim() && <span className="inline-flex items-center gap-1">esc clears the search</span>}
            </div>
          )}
          {err && <FieldError>{err}</FieldError>}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loadErr ? <LoadError what="prompts" error={loadErr} onRetry={() => setLoadAttempt((n) => n + 1)} /> : items === null ? (
              <div className="flex h-40 items-center justify-center"><Loader2 size={18} className="animate-spin text-on-surface-low" /></div>
            ) : items.length === 0 ? (
              <div data-type="body-s" className="flex h-40 flex-col items-center justify-center gap-1 px-4 text-center text-on-surface-low">
                No user prompts yet. Create one on the Prompts page.
              </div>
            ) : filtered?.length === 0 ? (
              <div data-type="body-s" className="flex h-40 items-center justify-center px-4 text-center text-on-surface-low">
                No prompts match “{q.trim()}”.
              </div>
            ) : (
              <PromptLibrary className="[&_[role=combobox]]:hidden" prompts={(filtered ?? []).map(prompt => ({ id: prompt.name, name: prompt.title || prompt.name,
                body: prompt.content ?? prompt.description ?? '', variables: (prompt.variables ?? []).map(variable => variable.name) }))}
                query="" selectedId={filtered?.some(prompt => prompt.name === selectedName) ? selectedName : filtered?.[0]?.name || ''} onQueryChange={setQ}
                onSelect={name => { if (loadingDetail) return; setSelectedName(name); const prompt = items.find(item => item.name === name); if (prompt) void pick(prompt) }} />
            )}
          </div>
        </div>
      )}
    </Modal>
  )
}

function FillIn({ prompt, onBack, onInsert, onSend }: {
  prompt: PromptItem
  onBack: () => void
  onInsert: (text: string) => void
  onSend?: (text: string) => void
}) {
  const vars: PromptVariable[] = prompt.merged_variables?.length ? prompt.merged_variables : (prompt.variables ?? [])
  const [values, setValues] = useState<Record<string, unknown>>(() => seedRenderValues(vars))
  const [preview, setPreview] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const debTimer = useRef<number | null>(null)

  useEffect(() => {
    if (debTimer.current) window.clearTimeout(debTimer.current)
    debTimer.current = window.setTimeout(() => {
      api.renderPrompt(prompt.name, values)
        .then((r) => { setPreview(r.rendered); setErr('') })
        .catch((e) => setErr(e instanceof Error ? e.message : 'render failed'))
    }, 250)
    return () => { if (debTimer.current) window.clearTimeout(debTimer.current) }
  }, [prompt.name, values])

  const missingRequired = vars.some((v) => v.required && !String(values[v.name] ?? '').trim())

  async function finalize(send: boolean) {
    setBusy(true); setErr('')
    try {
      const r = await api.renderPrompt(prompt.name, values)
      if (send && onSend) onSend(r.rendered)
      else onInsert(r.rendered)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'render failed'); setBusy(false)
    }
  }

  return (
    <div className="flex min-h-[320px] flex-col gap-3"
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !busy && !missingRequired) {
          e.preventDefault(); void finalize(!!onSend)
        }
      }}>
      <button type="button" onClick={onBack} data-type="body-s" className="inline-flex items-center gap-1 self-start text-on-surface-low hover:text-on-surface">
        <ChevronLeft size={14} /> All prompts
      </button>
      {prompt.description && <p data-type="body-s" className="text-on-surface-var">{prompt.description}</p>}

      <div className="flex flex-col gap-2">
        {vars.map((v) => (
          <label key={v.name} className="flex flex-col gap-1">
            <span data-type="caption" className="text-on-surface-var">{v.name}{v.required && <span className="text-danger"> *</span>}{v.description && <span className="text-on-surface-low"> — {v.description}</span>}</span>
            <VarInput v={v} value={values[v.name]} onChange={(val) => setValues((s) => ({ ...s, [v.name]: val }))} />
          </label>
        ))}
      </div>

      <div>
        <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">Preview</div>
        <pre data-type="body-s" className="max-h-44 overflow-y-auto rounded-md bg-surface-container px-3 py-2 text-on-surface-var whitespace-pre-wrap break-words">{preview || '…'}</pre>
      </div>

      {err && <FieldError>{err}</FieldError>}

      <div className="flex items-center justify-end gap-2 border-t border-outline-variant/40 pt-3">
        <Button variant="ghost" size="sm" onClick={onBack}>Cancel</Button>
        <Button variant="secondary" size="sm" disabled={busy || missingRequired} disabledReason={missingRequired && !busy ? 'Fill in the required variables first' : BUSY_REASON} onClick={() => finalize(false)}>Insert</Button>
        {onSend && <Button size="sm" disabled={busy || missingRequired} disabledReason={missingRequired && !busy ? 'Fill in the required variables first' : BUSY_REASON} onClick={() => finalize(true)}><CornerDownLeft size={14} /> Send</Button>}
      </div>
    </div>
  )
}

function VarInput({ v, value, onChange }: { v: PromptVariable; value: unknown; onChange: (v: unknown) => void }) {
  const base = 'w-full rounded-md bg-surface-container px-2.5 py-1.5 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  if (v.type === 'boolean') {
    return <Toggle on={!!value} onChange={(val) => onChange(val)} size="sm" />
  }
  if (v.type === 'select') {
    return (
      <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} data-type="body-s" className={`${base}`}>
        <option value="">—</option>
        {(v.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  if (v.type === 'textarea') {
    return <textarea value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} rows={3} data-type="body-s" className={`${base} resize-y`} />
  }
  if (v.type === 'number') {
    return <input type="number" value={value === '' || value == null ? '' : Number(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} data-type="body-s" className={base} />
  }
  return <input value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} data-type="body-s" className={base} />
}
