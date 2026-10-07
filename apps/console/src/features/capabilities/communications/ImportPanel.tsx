import { Select, Field, TextArea } from '../../../shared/ui/forms'
import { useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { BUSY_REASON } from '../../../shared/ui/unavailable'

type Match = { id: string; name: string; revision: number }
type Row = { row_id: string; name: string; error: string; candidate: { identities: { kind: string; value: string }[] } | null; matches: Match[] }
type Preview = { source_digest: string; rows: Row[] }
type Receipt = { source_digest: string; committed_at: string; rows: { row_id: string; action: string; person_id: string | null }[] }
const base = '/api/capabilities/communications/import'

export function ImportPanel({ onImported }: { onImported: () => void }) {
  const [format, setFormat] = useState('csv')
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const invalidate = () => { setPreview(null); setReceipt(null); setError('') }
  async function inspect() {
    setBusy(true); setError(''); setReceipt(null)
    try {
      const result = await requestJson<Preview>(`${base}/preview`, 'POST', { format, content })
      setPreview(result); setChoices(Object.fromEntries(result.rows.map(row => [row.row_id, 'skip'])))
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); setPreview(null) } finally { setBusy(false) }
  }
  async function apply() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const decisions = preview.rows.map(row => {
        const action = choices[row.row_id]
        const match = row.matches.find(item => item.id === action)
        return match ? { row_id: row.row_id, action: 'update', person_id: match.id, revision: match.revision } : { row_id: row.row_id, action }
      })
      const result = await requestJson<{ receipt: Receipt }>(`${base}/commit`, 'POST', { format, content, source_digest: preview.source_digest, decisions })
      setReceipt(result.receipt); onImported()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return <section aria-label="Import contacts" className="space-y-l">
    <h2 data-type="title-m">Import contacts</h2>
    <p>Preview CSV or UTF-8 vCard 3/4. CSV columns: name, email, phone, handle, notes. Separate multiple identities with |. Nothing is saved until you commit.</p>
    <Field label={"Contact format"}><Select value={format} disabled={busy} onChange={nextValue => { setFormat(nextValue); invalidate() }} options={[{ value: "csv", label: "CSV" }, { value: "vcard", label: "vCard" }]} /></Field>
    <Field label={"Contact data"}><TextArea value={content} maxLength={262144} disabled={busy} onChange={nextValue => { setContent(nextValue); invalidate() }} /></Field>
    <Button onClick={inspect} disabled={busy || !content.trim()} disabledReason={busy ? BUSY_REASON : undefined}>Preview contacts</Button>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {preview && <><p>Choose what to import. Updating a match adds identities and preserves existing name, notes, ring and cadence. Conflicts never merge people automatically.</p>
      <ul>{preview.rows.map(row => <li key={row.row_id} className="my-3 border-b border-outline pb-3">
        <strong>{row.name || `Contact ${row.row_id}`}</strong>
        <p>{row.candidate?.identities.map(i => `${i.kind}: ${i.value}`).join(' · ')}</p>
        {row.error && <p>{row.error}</p>}
        {row.matches.length > 1 && <p>Multiple people match. Resolve the conflicting identities before importing this row.</p>}
        <Field label={"Decision for contact" + " " + String(row.row_id)}><Select ariaLabel={`Decision for contact ${row.row_id}`} disabled={busy || !!receipt} value={choices[row.row_id]} onChange={nextValue => setChoices({ ...choices, [row.row_id]: nextValue })} options={[{ value: "skip", label: "Skip" }, ...((!row.error && !row.matches.length) ? [{ value: "create", label: "Create person" }] : []), ...((!row.error && row.matches.length === 1) ? row.matches.map(match => ({ value: match.id, label: "Add identities to " + String(match.name) })) : [])]} /></Field>
      </li>)}</ul><Button onClick={apply} disabled={busy || !!receipt || Object.values(choices).every(c => c === 'skip')} disabledReason={busy ? BUSY_REASON : undefined}>Commit selected contacts</Button>
    </>}
    {receipt && <div role="status"><p>Import saved at {receipt.committed_at}.</p><ul>{receipt.rows.map(row => <li key={row.row_id}>{row.action === 'skip' ? 'Skipped' : <a className="text-primary underline" href={`#/capabilities/communications?person=${row.person_id}`}>Open imported person</a>}</li>)}</ul></div>}
  </section>
}
