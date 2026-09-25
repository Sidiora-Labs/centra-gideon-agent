import { useEffect, useRef, useState } from 'react'
import { gatewayHeaders, requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Conversation = { id: string; title: string; created_at: string | null; message_count: number; branch_count: number; unsupported_parts: number; valid: boolean; error: string; existing_id: string | null }
type Preview = { source_digest: string; total: number; conversations: Conversation[] }
type Receipt = { request_id: string; source_item_id: string; source_link: string; items: { conversation_id: string; source_link: string; status: string }[] }
const root = '/api/capabilities/knowledge/archives'

export default function ArchivePage() {
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const [next, setNext] = useState<number | null>(null)
  const key = useRef(crypto.randomUUID())
  useEffect(() => {
    let active = true
    requestJson<{ items: Receipt[]; next_offset: number | null }>(`${root}?offset=${offset}`).then(result => { if (active) { setReceipts(result.items); setNext(result.next_offset) } }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [offset])
  function changed(value: string) { setContent(value); setPreview(null); setSelected([]); key.current = crypto.randomUUID(); setError('') }
  async function review() {
    setBusy(true); setError(''); setPreview(null)
    try { const result = await requestJson<Preview>(root + '/preview', 'POST', { format: 'chatgpt', content }); setPreview(result); setSelected([]) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function commit() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const result = await requestJson<Receipt>(root + '/commit', 'POST', { request_id: key.current, source_digest: preview.source_digest, format: 'chatgpt', content, conversation_ids: selected })
      setReceipts(current => [result, ...current.filter(item => item.request_id !== result.request_id)])
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function download(identity: string) {
    try {
      const response = await fetch(`${root}/sources/${encodeURIComponent(identity)}`, { headers: gatewayHeaders })
      if (!response.ok) throw new Error((await response.json()).error || 'Original archive unavailable')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a'); link.href = url; link.download = 'conversations.json'; link.click(); URL.revokeObjectURL(url)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Conversation archives</h1>
    <p>Import a ChatGPT conversations.json export, up to 512 KiB and 500 conversations. Review each conversation before importing. All branches and original data are retained; unsupported attachments are reported.</p>
    {error && <p role="alert">{error}</p>}
    <label className="block">Archive file<input aria-label="Archive file" type="file" accept="application/json,.json" disabled={busy} onChange={async e => { const file = e.target.files?.[0]; if (file) { if (file.size > 524288) { setError('Archive exceeds 512 KiB'); return } changed(await file.text()) } }} /></label>
    <label className="block">Export JSON<textarea aria-label="Export JSON" className="block min-h-48 w-full border p-3" disabled={busy} value={content} onChange={e => changed(e.target.value)} /></label>
    <Button disabled={busy || !content.trim()} onClick={() => void review()}>Preview archive</Button>
    {preview && <section aria-label="Conversation selection"><p>{preview.total} conversations found</p>{preview.conversations.map(item => <article key={item.id} className="my-3 border p-3"><label><input type="checkbox" aria-label={`Import ${item.title}`} disabled={busy || !item.valid} checked={selected.includes(item.id)} onChange={e => { setSelected(current => e.target.checked ? [...current, item.id] : current.filter(id => id !== item.id)); key.current = crypto.randomUUID() }} />{item.title}</label><p>{item.created_at ? new Date(item.created_at).toLocaleString() : 'Original date unavailable'} · {item.message_count} messages · {item.branch_count} branch points · {item.unsupported_parts} unsupported parts</p>{item.existing_id && <p>Already imported; a repeat will keep the existing record.</p>}{!item.valid && <p role="alert">{item.error}</p>}</article>)}<Button disabled={busy || selected.length === 0} onClick={() => void commit()}>Import selected conversations</Button></section>}
    <section aria-label="Archive history"><h2>Archive history</h2>{receipts.length === 0 && <p>No archive imports yet.</p>}{receipts.map(receipt => <article key={receipt.request_id} className="my-3 border p-3"><a href={receipt.source_link}>Open original source</a> <Button onClick={() => void download(receipt.source_item_id)}>Download original JSON</Button>{receipt.items.map(item => <p key={item.conversation_id}><a href={item.source_link}>Open conversation {item.conversation_id}</a> · {item.status}</p>)}</article>)}<Button disabled={offset === 0 || busy} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous archives</Button><Button disabled={next === null || busy} onClick={() => setOffset(next!)}>Next archives</Button></section>
  </main>
}
