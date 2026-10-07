import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import {ListScaffold} from '../../../shared/ui/ListScaffold'
import { Checkbox, TextInput, TextArea, Select } from '../../../shared/ui/forms'
import { Button } from '../../../shared/ui/Button'
type Part = { id: string; name: string; entry_beats: number; notation: string; catalog_ref: {track_id:string;render_id:string}|null }
type Round = {id:string;title:string;tempo_bpm:number;meter_beats:number;notes:string;parts:Part[];partner_ids:string[];revision:number;archived:boolean}
type Practice = {id:string;grade:number;occurred_at:string;part_ids:string[];notes:string;round_revision:number}
type Track = {id:string;title:string;renders:{id:string;artifact_ref:{slug:string;version:number};source:{kind:string}}[]}
const blankPart = (): Part => ({id:crypto.randomUUID(),name:'Voice',entry_beats:0,notation:'',catalog_ref:null})
const selected = () => window.location.hash.split('/rounds/')[1] || ''
export default function RoundsPage({apiBase='/api/capabilities/music/rounds',catalogBase='/api/capabilities/music/catalog'}:{apiBase?:string;catalogBase?:string}) {
  const [id,setId]=useState(selected),[rows,setRows]=useState<Round[]>([]),[item,setItem]=useState<Round|null>(null)
  const [draft,setDraft]=useState({title:'',tempo_bpm:100,meter_beats:4,notes:'',parts:[blankPart()],partner_ids:[] as string[]})
  const [history,setHistory]=useState<Practice[]>([]),[tracks,setTracks]=useState<Track[]>([]),[chosen,setChosen]=useState<string[]>([])
  const [grade,setGrade]=useState(3),[practiceNotes,setPracticeNotes]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[dirty,setDirty]=useState(false),[offset,setOffset]=useState(0)
  const [catalogError,setCatalogError]=useState(''),[catalogLoading,setCatalogLoading]=useState(false),[catalogRevision,setCatalogRevision]=useState(0)
  async function request(path:string,method='GET',data?:unknown){const response=await fetch(apiBase+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw new Error(value.message||value.error);return value}
  function show(value:Round){setItem(value);setDraft({title:value.title,tempo_bpm:value.tempo_bpm,meter_beats:value.meter_beats,notes:value.notes,parts:value.parts,partner_ids:value.partner_ids});setChosen(value.parts.map(part=>part.id));setDirty(false)}
  function open(value:string){window.location.hash=`#/capabilities/music/rounds${value?'/'+value:''}`;setId(value)}
  useEffect(()=>{const changed=()=>setId(selected());window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed)},[])
  useEffect(() => {
    let active = true
    setCatalogLoading(true); setCatalogError(''); setTracks([])
    fetch(catalogBase + '/tracks?limit=100')
      .then(async response => {
        if (!response.ok) throw new Error(`Recordings request failed (${response.status})`)
        const value = await response.json()
        if (!Array.isArray(value.items)) throw new Error('The recordings response is invalid')
        return value.items as Track[]
      })
      .then(value => { if (active) setTracks(value) })
      .catch(reason => { if (active) setCatalogError(reason instanceof Error ? reason.message : String(reason)) })
      .finally(() => { if (active) setCatalogLoading(false) })
    return () => { active = false }
  }, [catalogBase, catalogRevision])
  useEffect(()=>{if(id&&item?.id===id)return;let active=true;setLoading(true);setItem(null);setError('');request(id?'/'+id:`?offset=${offset}&limit=25`).then(value=>{if(!active)return;if(id)show(value.item);else{setRows(value.items);setDraft({title:'',tempo_bpm:100,meter_beats:4,notes:'',parts:[blankPart()],partner_ids:[]});setDirty(false)}}).catch(err=>{if(active)setError(err.message)}).finally(()=>{if(active)setLoading(false)});if(id)request('/'+id+'/practice').then(value=>{if(active)setHistory(value.items)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[id,offset,apiBase])
  function edit(changes:Partial<typeof draft>){setDraft(value=>({...value,...changes}));setDirty(true)}
  function editPart(index:number,changes:Partial<Part>){edit({parts:draft.parts.map((part,i)=>i===index?{...part,...changes}:part)})}
  async function save(){setBusy(true);setError('');try{const value=await request(id?'/'+id:'',id?'PATCH':'POST',{...draft,...(item?{revision:item.revision}:{})});show(value.item);if(!id)open(value.item.id)}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
  async function practice(){if(!item)return;setBusy(true);setError('');try{const value=await request('/'+id+'/practice','POST',{request_id:crypto.randomUUID(),round_revision:item.revision,part_ids:chosen,occurred_at:new Date().toISOString(),grade,notes:practiceNotes});setHistory(previous=>[...previous.filter(row=>row.id!==value.item.id),value.item]);setPracticeNotes('')}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
  return <ListScaffold title="Musical canons and part practice" bodyClassName="mx-auto flex w-full max-w-4xl flex-col gap-l px-l py-xl">
    {error&&<p role="alert">{error}</p>}
    {catalogLoading && <p role="status">Loading recordings…</p>}
    {catalogError && <div className="space-y-s"><p role="alert">Could not load recordings: {catalogError}</p><Button variant="secondary" disabled={catalogLoading} disabledReason={catalogLoading ? BUSY_REASON : undefined} onClick={() => setCatalogRevision(value => value + 1)}>Retry recordings</Button></div>}
    {loading?<p role="status">Loading canons…</p>:<>
      {!id&&<><ul>{rows.map(row=><li key={row.id}><a className="text-primary" href={`#/capabilities/music/rounds/${row.id}`} onClick={()=>setId(row.id)}>{row.title}</a> · {row.parts.length} parts · {row.id}</li>)}</ul>{rows.length===0&&<p>No canons yet.</p>}<div className="flex gap-2"><Button disabled={!offset} onClick={()=>setOffset(value=>Math.max(0,value-25))} disabledReason={!offset ? "This is the first page; there is no previous page. Explain the separate loading branch where present." : undefined}>Previous</Button><Button disabled={rows.length<25} onClick={()=>setOffset(value=>value+25)} disabledReason={rows.length<25 ? "There is no next page. Explain the separate loading branch where present." : undefined}>Next</Button></div></>}
      {id&&<Button variant="secondary" onClick={()=>open('')}>All canons</Button>}
      {(!id||item)&&<form className="flex flex-col gap-3" onSubmit={event=>{event.preventDefault();void save()}}>
        <label>Canon title<TextInput required maxLength={200} value={draft.title} onChange={(nextValue) => edit({ title: nextValue })}/></label>
        <label>Tempo BPM<TextInput type="number" min={20} max={300} value={String(draft.tempo_bpm)} onChange={(nextValue) => edit({ tempo_bpm: Number(nextValue) })}/></label>
        <label>Beats per measure<TextInput type="number" min={1} max={16} value={String(draft.meter_beats)} onChange={(nextValue) => edit({ meter_beats: Number(nextValue) })}/></label>
        <label>Arrangement notes<TextArea value={draft.notes} onChange={(nextValue) => edit({ notes: nextValue })} className="min-h-24"/></label>
        <label>Partner canon IDs (one per line)<TextArea value={draft.partner_ids.join('\n')} onChange={(nextValue) => edit({ partner_ids: nextValue.split('\n').map(value => value.trim()).filter(Boolean) })} className="min-h-24"/></label>
        {draft.parts.map((part,index)=><fieldset className="rounded-lg border border-outline p-3" key={part.id}><legend>Part {index+1}</legend>
          <label>Part {index+1} name<TextInput value={part.name} onChange={(nextValue) => editPart(index, { name: nextValue })}/></label>
          <label>Part {index+1} entry beat<TextInput type="number" min={0} step="any" value={String(part.entry_beats)} onChange={(nextValue) => editPart(index, { entry_beats: Number(nextValue) })}/></label>
          <p>Entry at {(part.entry_beats*60/draft.tempo_bpm).toFixed(2)} seconds</p>
          <label>Part {index+1} notation<TextArea value={part.notation} onChange={(nextValue) => editPart(index, { notation: nextValue })} className="min-h-24"/></label>
          <label>Part {index+1} recording<Select value={String(part.catalog_ref ? part.catalog_ref.track_id + ':' + part.catalog_ref.render_id : '')} onChange={(nextValue) => { const [track_id, render_id] = nextValue.split(':'); editPart(index, { catalog_ref: track_id ? { track_id, render_id } : null }); }} options={[({ value: "", label: "No recording attached" }), ...tracks.flatMap(track => track.renders.filter(render => render.source.kind === 'imported').map(render => ({ value: String(track.id + ':' + render.id), label: String(track.title) + " \u00B7 " + String(render.artifact_ref.slug) })))]}/></label>
          <Button variant="secondary" disabled={draft.parts.length<=1} onClick={()=>edit({parts:draft.parts.filter((_,i)=>i!==index)})} disabledReason={draft.parts.length<=1 ? "A round must retain at least one voice part." : undefined}>Remove part {index+1}</Button>
        </fieldset>)}
        <Button variant="secondary" disabled={draft.parts.length>=16} onClick={()=>edit({parts:[...draft.parts,blankPart()]})} disabledReason={draft.parts.length>=16 ? "The round already has the maximum 16 voice parts." : undefined}>Add voice part</Button>
        <Button type="submit" disabled={busy||!draft.title.trim()} disabledReason={busy ? BUSY_REASON : !draft.title.trim() ? 'Enter an arrangement title before saving.' : undefined}>{item?'Save arrangement':'Create canon'}</Button>
      </form>}
      {item&&<article aria-label="Part practice" className="flex flex-col gap-3"><h2>{item.title}</h2><p>Practice uses saved arrangement revision {item.revision}.</p>
        {item.parts.map(part=>{const track=tracks.find(row=>row.id===part.catalog_ref?.track_id);const ref=track?.renders.find(row=>row.id===part.catalog_ref?.render_id)?.artifact_ref;return <div key={part.id}><label className="flex items-center gap-s"><Checkbox ariaLabel={part.name} checked={chosen.includes(part.id)} onChange={checked=>setChosen(values=>checked?[...values,part.id]:values.filter(value=>value!==part.id))}/>{part.name}</label><pre className="whitespace-pre-wrap font-sans">{part.notation}</pre>{ref?<audio aria-label={`Play ${part.name}`} controls src={`/api/artifacts/${ref.slug}/raw?version=${ref.version}`}/>:<p>{part.catalog_ref?'Recording unavailable.':'No recording attached.'}</p>}</div>})}
        <label>Practice grade<Select value={String(grade)} onChange={(nextValue) => setGrade(Number(nextValue))} options={[...[0, 1, 2, 3, 4, 5].map(value => ({ value: String(value), label: String(value) }))]}/></label>
        <label>Practice notes<TextArea value={practiceNotes} onChange={(nextValue) => setPracticeNotes(nextValue)} className="min-h-24"/></label>
        <Button disabled={busy||dirty||chosen.length===0} disabledReason={busy ? BUSY_REASON : dirty ? 'Save arrangement changes before practicing.' : chosen.length === 0 ? 'Choose at least one part before logging practice.' : undefined} onClick={()=>void practice()}>Log part practice</Button>{dirty&&<p>Save arrangement changes before practicing.</p>}
        <ol aria-label="Part practice history">{history.map(row=><li key={row.id}>Grade {row.grade} · {row.part_ids.length} parts · revision {row.round_revision} · {row.notes}</li>)}</ol>
      </article>}
    </>}
  </ListScaffold>
}
