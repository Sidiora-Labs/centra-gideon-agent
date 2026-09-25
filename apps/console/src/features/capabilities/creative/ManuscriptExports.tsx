import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Export = { id: string; source_kind: string; source_id: string; source_revision: number; metadata: { title: string; creator: string; language: string; identifier: string }; selections: { title: string; work_revision: number; artifact_id: string; artifact_version: number; sha256: string }[]; files: Record<'epub' | 'print', { path: string; bytes: number; sha256: string; mime: string }> }

export default function ManuscriptExports({ baseUrl = '', apiRoot = '/api/capabilities/creative/exports' }: { baseUrl?: string; apiRoot?: string }) {
  const root=baseUrl+apiRoot
  const [items,setItems]=useState<Export[]>([]),[kind,setKind]=useState('work'),[sourceId,setSourceId]=useState(''),[revision,setRevision]=useState('1'),[title,setTitle]=useState(''),[creator,setCreator]=useState(''),[language,setLanguage]=useState('en'),[identifier,setIdentifier]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const load=async()=>setItems((await readJson<{items:Export[]}>(await gatewayRequest(root))).items)
  useEffect(()=>{void load().catch(reason=>setError(String(reason)))},[root])
  const create=async()=>{setBusy(true);setError('');try{await readJson(await gatewayRequest(root,'POST',{request_id:crypto.randomUUID(),source_kind:kind,source_id:sourceId,source_revision:Number(revision),title,creator,language,identifier}));await load()}catch(reason){setError(String(reason))}finally{setBusy(false)}}
  return <section aria-label="Manuscript exports" className="space-y-4"><h2>EPUB and print manuscript export</h2><p>Exports pin the selected work or ordered series revision and every immutable manuscript artifact. Downloads are regenerated only through a new reviewed export.</p>{error&&<p role="alert">{error}</p>}
    <label>Manuscript source<select aria-label="Manuscript source" value={kind} onChange={event=>setKind(event.target.value)}><option value="work">Writing work</option><option value="series">Ordered series</option></select></label>
    <label>Source ID<input aria-label="Export source ID" value={sourceId} onChange={event=>setSourceId(event.target.value)}/></label><label>Source revision<input aria-label="Export source revision" type="number" min="1" value={revision} onChange={event=>setRevision(event.target.value)}/></label>
    <label>Book title<input aria-label="Export title" value={title} onChange={event=>setTitle(event.target.value)}/></label><label>Creator<input aria-label="Export creator" value={creator} onChange={event=>setCreator(event.target.value)}/></label><label>Language<input aria-label="Export language" value={language} onChange={event=>setLanguage(event.target.value)}/></label><label>Publication identifier<input aria-label="Export identifier" value={identifier} onChange={event=>setIdentifier(event.target.value)}/></label>
    <Button disabled={busy||!sourceId||!creator||!language||!identifier||Number(revision)<1} onClick={()=>void create()}>Create EPUB and print PDF</Button>
    {items.map(item=><article key={item.id} aria-label={`Manuscript export ${item.metadata.title}`}><h3>{item.metadata.title}</h3><p>{item.source_kind} {item.source_id} revision {item.source_revision} · {item.selections.length} manuscript sections</p>{item.selections.map((row,index)=><p key={row.artifact_id+index}>{row.title} · work revision {row.work_revision} · artifact {row.artifact_id} version {row.artifact_version} · SHA-256 {row.sha256}</p>)}<a href={baseUrl+item.files.epub.path}>Download EPUB ({item.files.epub.bytes} bytes)</a> <a href={baseUrl+item.files.print.path}>Download print PDF ({item.files.print.bytes} bytes)</a></article>)}
  </section>
}
