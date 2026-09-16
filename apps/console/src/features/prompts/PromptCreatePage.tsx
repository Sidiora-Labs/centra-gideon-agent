import { usePromptRequest } from './promptEditorState'
import { useRef, useState, useEffect } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { Segmented } from '../../shared/ui/Segmented'
import { PageTitle } from '../../shared/ui/PageTitle'
import { api, type PromptKind } from '../../shared/data/api'
import { PromptForm, emptyDraft, draftToPayload, type PromptDraft } from './PromptForm'
import { SnippetForm, emptySnippetDraft, snippetDraftToPayload, type SnippetDraft } from './SnippetForm'
import { PromptPreviewPane } from './PromptPreviewPane'
import { SyntaxReference } from './SyntaxReference'

export function PromptCreatePage({ onBack, onCreated, mode = 'user' }: {
  onBack: () => void
  onCreated: () => void
  mode?: PromptKind | 'snippets'
}) {
  const isSnippet = mode === 'snippets'
  const [kind, setKind] = useState<PromptKind>(mode === 'system' ? 'system' : 'user')
  const [draft, setDraft] = useState<PromptDraft>(() => emptyDraft(mode === 'system' ? 'system' : 'user'))
  const [snipDraft, setSnipDraft] = useState<SnippetDraft>(() => emptySnippetDraft())
  const request = usePromptRequest(mode)
  const { busy: saving, error: err, setError: setErr } = request
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])
  const [rail, setRail] = useState<'preview' | 'reference'>('preview')
  const insertRef = useRef<(text: string) => void>(() => {})
  const previewDraft: PromptDraft = isSnippet
    ? { ...emptyDraft(), name: snipDraft.name, content: snipDraft.content, variables: snipDraft.variables, tags: snipDraft.tags }
    : draft

  const name = isSnippet ? snipDraft.name : draft.name

  const create = () => {
    if (!name.trim()) { setErr('Name is required'); return }
    void request.run(async () => {
      if (isSnippet) await api.createSnippet(snippetDraftToPayload(snipDraft))
      else await api.createPrompt(draftToPayload({ ...draft, kind }))
    }, onCreated, 'Create failed')
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>{isSnippet ? 'New snippet' : 'New prompt'}</PageTitle></div>} />

      <div className="flex-1 overflow-hidden">
        <div className="mx-auto flex h-full flex-col gap-l overflow-y-auto px-l py-l lg:flex-row lg:gap-xl lg:overflow-hidden"
          style={{ maxWidth: 'var(--content-width)' }}>
          <div className="flex min-w-0 flex-1 flex-col gap-l lg:overflow-y-auto lg:pb-2xl lg:pr-1">
            {!isSnippet && (
              <div className="flex items-center gap-m">
                <span data-type="body-s" className="text-on-surface-var">Kind</span>
                <Segmented ariaLabel="Prompt kind" value={kind} onChange={(v) => setKind(v as PromptKind)} options={[{ key: 'user', label: 'User' }, { key: 'system', label: 'System' }]} />
                <span data-type="caption" className="text-on-surface-low">{kind === 'system' ? 'Bound to a use-case, injected as the system prompt.' : 'Invoked in chat with filled-in variables.'}</span>
              </div>
            )}
            {isSnippet
              ? <SnippetForm draft={snipDraft} onChange={setSnipDraft} registerInsert={(fn) => { insertRef.current = fn }} />
              : <PromptForm draft={draft} onChange={setDraft} registerInsert={(fn) => { insertRef.current = fn }} />}
            {err && <p ref={errRef} role="alert" data-type="body-s" className="text-danger">{err}</p>}
          </div>

          <aside className="flex shrink-0 flex-col lg:w-[380px] lg:overflow-hidden">
            <div className="mb-2 shrink-0">
              <Segmented ariaLabel="Right rail" value={rail} onChange={(v) => setRail(v as 'preview' | 'reference')}
                options={[{ key: 'preview', label: 'Preview' }, { key: 'reference', label: 'Reference' }]} />
            </div>
            <div className="min-h-0 flex-1 rounded-lg border border-outline-variant/30 bg-surface-container/20 p-m lg:overflow-y-auto">
              {rail === 'preview'
                ? <PromptPreviewPane draft={previewDraft} />
                : <SyntaxReference onInsert={(s) => insertRef.current(s)} />}
            </div>
          </aside>
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onBack}>Cancel</Button>
          <Button onClick={create} loading={saving} loadingLabel="Creating…" disabled={saving || !name.trim()} disabledReason={!name.trim() ? 'Enter a name first' : undefined}><Check size={16} /> {isSnippet ? 'Create snippet' : 'Create prompt'}</Button>
        </div>
      </div>
    </div>
  )
}
