import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Check } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { IconButton } from '../../shared/ui/IconButton'
import { Button } from '../../shared/ui/Button'
import { PageTitle } from '../../shared/ui/PageTitle'
import { api } from '../../shared/data/api'
import { AgentForm, emptyDraft, draftToPayload } from './AgentForm'
import { useAgentWrite } from './agentEditorState'

export function AgentCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [draft, setDraft] = useState(emptyDraft)
  const { busy, error: err, setError, write } = useAgentWrite('create-agent')
  const errRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => { if (err) errRef.current?.scrollIntoView({ block: 'nearest' }) }, [err])
  const create = () => {
    if (!draft.name.trim()) { setError('Name is required'); return }
    void write(() => api.createAgent({ ...draftToPayload(draft), provider: 'native', source: 'gideon' }), onCreated, 'Create failed')
  }
  return <div className="flex h-full flex-col bg-surface">
    <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><PageTitle>New agent</PageTitle></div>} />
    <div className="min-h-0 flex-1 overflow-y-auto"><div className="mx-auto px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}><AgentForm draft={draft} onChange={setDraft} />{err && <p ref={errRef} role="alert" data-type="body-s" className="mt-l rounded-md border-l-2 border-danger bg-danger/10 p-m text-danger">{err}</p>}</div></div>
    <footer className="shrink-0 border-t border-outline-variant/40 bg-surface-container/30 px-l py-m"><div className="mx-auto flex justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}><Button variant="ghost" onClick={onBack}>Cancel</Button><Button onClick={create} loading={busy} loadingLabel="Creating…" disabled={busy || !draft.name.trim()} disabledReason={!draft.name.trim() ? 'Enter an agent name first' : undefined}><Check size={16} /> Create agent</Button></div></footer>
  </div>
}
