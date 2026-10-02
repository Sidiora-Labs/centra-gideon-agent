import { useEffect, useMemo, useState } from 'react'
import { Anchor, CheckCircle2, FileClock, GitBranch, RefreshCw, ShieldAlert } from 'lucide-react'
import { api, ApiError, type HypermidMemoryWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { confirm } from '../../shared/ui/dialog'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { SearchField } from '../../shared/ui/SearchField'
import { Surface } from '../../shared/ui/Surface'
import { TextArea } from '../../shared/ui/forms'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'
import { beginConflictDraft, inspectionQueryKey, reapplyConflict, reloadConflict, retainRejectedDraft, type ConflictDraft } from './hypermidState'
import { MemoryProvenance } from './MemoryProvenance'

const KINDS = ['', 'anchor', 'summary', 'fact', 'episode', 'note', 'smart_note'] as const

function recordLabel(record: HypermidMemoryWire): string {
  return record.title || record.content?.split('\n')[0].slice(0, 96) || `${record.kind.replaceAll('_', ' ')} memory`
}

function MemoryProvenanceStatus({ memoryId }: { memoryId: string }) {
  const provenance = useQuery(`hypermid:memory:provenance:${memoryId}`, () => api.hypermidMemoryProvenance(memoryId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!provenance.data && provenance.error) return <div className="mt-l rounded-lg bg-surface-high p-m">
    <p className="text-sm text-on-surface">Memory provenance unavailable</p>
    <p data-type="caption" className="mt-xs text-on-surface-low">The authenticated provenance authority did not return metadata for this record.</p>
    <Button size="sm" variant="secondary" className="hypermid-touch mt-s" onClick={provenance.refresh}>Retry</Button>
  </div>
  if (!provenance.data) return <p role="status" className="mt-l text-sm text-on-surface-low">Loading memory provenance…</p>
  const promote = async (expectedRevision: number) => {
    if (!(await confirm({
      title: 'Review this memory as an instruction?',
      body: 'Promotion changes the trust class for this exact memory revision. It does not grant provider, route, credential, budget, or tool authority.',
      confirmLabel: 'Promote reviewed revision',
    }))) return
    setBusy(true); setError('')
    try {
      await api.promoteHypermidMemory(memoryId, expectedRevision)
      provenance.refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The provenance promotion was refused.')
    } finally { setBusy(false) }
  }
  return <div className="mt-l">
    <MemoryProvenance memory={provenance.data} busy={busy} onPromote={(_, revision) => void promote(revision)} />
    {error && <p role="alert" className="mt-s text-sm text-danger">{error}</p>}
  </div>
}

function MemoryDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const record = useQuery(`hypermid:memory:record:${id}`, () => api.hypermidMemoryRecord(id))
  const [edit, setEdit] = useState<ConflictDraft<string>>()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (record.data && (!edit || edit.revision !== record.data.revision_digest)) {
      setEdit(beginConflictDraft(record.data.content || '', record.data.revision_digest))
    }
  }, [record.data?.id, record.data?.revision_digest])
  if (!record.data && record.error) return <LoadError what="memory evidence" error={record.error} onRetry={record.refresh} />
  if (!record.data || !edit) return <ListSkeleton rows={3} what="memory evidence" />
  const data = record.data
  const immutable = data.kind === 'anchor'
  const dirty = edit.draft !== edit.authoritative
  const save = async () => {
    setSaving(true); setError('')
    try {
      const result = await api.updateHypermidMemory(data.id, { content: edit.draft }, edit.revision)
      setEdit(beginConflictDraft(result.record.content || '', result.record.revision_digest))
      record.refresh(); onChanged()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const latest = await api.hypermidMemoryRecord(data.id)
          setEdit((current) => current && retainRejectedDraft(current, latest.content || '', latest.revision_digest,
            'This memory changed after you opened it. Your draft is still here.'))
        } catch { setError(caught.message) }
      } else setError(caught instanceof Error ? caught.message : 'The edit was refused.')
    } finally { setSaving(false) }
  }
  return <div>
    <div className="flex flex-wrap items-center gap-s">
      <StatusPill label={data.kind === 'anchor' ? 'Chronological anchor' : data.kind.replaceAll('_', ' ')} tone={data.kind === 'anchor' ? 'primary' : 'muted'} />
      <StatusPill label={data.state} tone={data.state === 'active' ? 'ok' : 'muted'} />
      {data.verified && <StatusPill label="verified" tone="ok" />}
      {data.contradicted && <StatusPill label="contradicted" tone="warn" />}
    </div>
    <h3 className="mt-m text-base text-on-surface">{recordLabel(data)}</h3>
    {immutable ? <div className="mt-m rounded-lg bg-primary/10 p-m text-sm text-on-surface">
      <div className="flex items-center gap-s font-medium"><Anchor size={15} /> Immutable chronological anchor</div>
      <p className="mt-xs whitespace-pre-wrap text-on-surface-low">{data.content || 'Content is not available to this scope.'}</p>
    </div> : <div className="mt-m">
      <TextArea value={edit.draft} onChange={(draft) => setEdit((current) => current && ({ ...current, draft }))}
        ariaLabel="Memory content" rows={7} />
      <div className="mt-s flex flex-wrap items-center justify-end gap-s">
        {dirty && <span data-type="caption" className="mr-auto text-on-surface-low">Unsaved draft</span>}
        <Button size="sm" variant="secondary" disabled={!dirty || saving} onClick={() => setEdit(beginConflictDraft(edit.authoritative, edit.revision))}>Discard draft</Button>
        <Button size="sm" loading={saving} disabled={!dirty} onClick={() => void save()}>Save memory</Button>
      </div>
    </div>}
    {edit.conflict && <div role="alert" className="mt-m rounded-lg border border-warn/40 bg-warn/10 p-m">
      <div className="flex items-center gap-s text-sm text-on-surface"><ShieldAlert size={16} className="text-warn" />{edit.conflict.message}</div>
      <div className="mt-m grid gap-s sm:grid-cols-2">
        <div><p data-type="caption" className="mb-xs text-on-surface-low">Your draft</p><p className="whitespace-pre-wrap rounded-md bg-surface p-s text-sm text-on-surface">{edit.draft}</p></div>
        <div><p data-type="caption" className="mb-xs text-on-surface-low">Current saved value</p><p className="whitespace-pre-wrap rounded-md bg-surface p-s text-sm text-on-surface">{edit.conflict.latest}</p></div>
      </div>
      <div className="mt-m flex flex-wrap justify-end gap-s">
        <Button size="sm" variant="secondary" onClick={() => setEdit(reloadConflict(edit))}>Reload saved value</Button>
        <Button size="sm" onClick={() => setEdit(reapplyConflict(edit))}>Keep my draft</Button>
      </div>
    </div>}
    {error && <p role="alert" className="mt-s text-sm text-danger">{error}</p>}
    <dl className="mt-l grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Source</dt><dd className="text-on-surface">{data.source_session_id ? 'Linked session' : 'No session source'}</dd></div>
      <div><dt className="text-on-surface-low">Embedding</dt><dd className="text-on-surface">{data.embedding?.state || 'Unknown'}{data.embedding?.dimensions ? ` · ${data.embedding.dimensions} dimensions` : ''}</dd></div>
      <div><dt className="text-on-surface-low">Retrieval score</dt><dd className="text-on-surface">{data.retrieval?.score == null ? 'Unknown' : data.retrieval.score.toFixed(3)}</dd></div>
      <div><dt className="text-on-surface-low">Suppressed candidates</dt><dd className="text-on-surface">{data.retrieval?.suppressed == null ? 'Unknown' : data.retrieval.suppressed}</dd></div>
    </dl>
    {data.lineage && data.lineage.length > 0 && <div className="mt-l"><p className="flex items-center gap-s text-sm text-on-surface"><GitBranch size={15} /> Lineage</p>
      <ul className="mt-s space-y-xs text-sm text-on-surface-low">{data.lineage.map((item) => <li key={`${item.relation}:${item.id}`}>{item.relation.replaceAll('_', ' ')}</li>)}</ul>
    </div>}
    <MemoryProvenanceStatus memoryId={data.id} />
  </div>
}

export function MemoryInspector() {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<(typeof KINDS)[number]>('')
  const [selected, setSelected] = useState('')
  const filters = useMemo(() => ({ q: query.trim() || undefined, kind: kind || undefined }), [query, kind])
  const memory = useQuery(inspectionQueryKey('memory', filters), () => api.hypermidMemory(filters))
  return <Section title="Memory evidence" hint="Review sources, revisions, lineage, verification, contradictions, embeddings, and retrieval evidence."
    right={<Button size="sm" variant="secondary" onClick={memory.refresh}><RefreshCw size={14} /> Refresh</Button>}>
    <div className="mb-m grid gap-s sm:grid-cols-[minmax(0,1fr)_12rem]">
      <SearchField value={query} onChange={setQuery} placeholder="Search scoped memory" ariaLabel="Search Hypermid memory" />
      <select value={kind} onChange={(event) => setKind(event.target.value as typeof kind)} aria-label="Memory kind"
        className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
        {KINDS.map((value) => <option key={value || 'all'} value={value}>{value ? value.replaceAll('_', ' ') : 'All memory types'}</option>)}
      </select>
    </div>
    {memory.error && !memory.data ? <LoadError what="Hypermid memory" error={memory.error} onRetry={memory.refresh} />
      : !memory.data ? <ListSkeleton what="Hypermid memory" />
      : memory.data.state === 'unavailable' || memory.data.state === 'unreadable'
        ? <LoadError what="Hypermid memory" error={memory.data.detail || memory.data.state} onRetry={memory.refresh} />
        : memory.data.items.length === 0 ? <EmptyState title="No matching memory" hint={query || kind ? 'Adjust the filters to see other scoped records.' : 'Accepted memory will appear here with its evidence.'} />
        : <div className="grid min-w-0 gap-m lg:grid-cols-[minmax(16rem,0.85fr)_minmax(0,1.4fr)]">
          <Surface tone="container" radius="lg" className="min-w-0 overflow-hidden">
            {memory.data.items.map((item) => <button key={item.id} type="button" onClick={() => setSelected(item.id)} aria-pressed={selected === item.id}
              className="flex min-h-11 w-full items-center gap-m border-b border-outline-variant/30 px-l py-m text-left last:border-0 hover:bg-surface-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary">
              {item.kind === 'anchor' ? <Anchor size={16} className="text-primary" /> : item.verified ? <CheckCircle2 size={16} className="text-success" /> : <FileClock size={16} className="text-on-surface-low" />}
              <span className="min-w-0 flex-1"><span className="block truncate text-sm text-on-surface">{recordLabel(item)}</span>
                <span data-type="caption" className="text-on-surface-low">{item.kind.replaceAll('_', ' ')} · {item.state}</span></span>
            </button>)}
          </Surface>
          <Surface tone="container" radius="lg" className="min-w-0 p-l">
            {selected ? <MemoryDetail id={selected} onChanged={memory.refresh} /> : <EmptyState title="Choose a memory" hint="Select a record to inspect its evidence and revision history." />}
          </Surface>
        </div>}
    {memory.data?.state === 'filtered' && <p data-type="caption" className="mt-s text-on-surface-low">Showing filtered memory. The complete scoped collection remains separate.</p>}
  </Section>
}
