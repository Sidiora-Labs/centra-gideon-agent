import { useEffect, useState } from 'react'
import {ListScaffold} from '../../../shared/ui/ListScaffold'
import {Checkbox} from '../../../shared/ui/forms'
import { Button } from '../../../shared/ui/Button'
type Part = { id: string; name: string; entry_beats: number; notation: string; catalog_ref: {track_id:string;render_id:string}|null }
type Round = {id:string;title:string;tempo_bpm:number;meter_beats:number;notes:string;parts:Part[];partner_ids:string[];revision:number;archived:boolean}
type Practice = {id:string;grade:number;occurred_at:string;part_ids:string[];notes:string;round_revision:number}
type Track = {id:string;title:string;renders:{id:string;artifact_ref:{slug:string;version:number};source:{kind:string}}[]}
const blankPart = (): Part => ({id:crypto.randomUUID(),name:'Voice',entry_beats:0,notation:'',catalog_ref:null})
const selected = () => window.location.hash.split('/rounds/')[1] || ''
const cls = 'h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
export default function RoundsPage({apiBase='/api/capabilities/music/rounds',catalogBase='/api/capabilities/music/catalog'}:{apiBase?:string;catalogBase?:string}) {
  const [id,setId]=useState(selected),[rows,setRows]=useState<Round[]>([]),[item,setItem]=useState<Round|null>(null)
  const [draft,setDraft]=useState({title:'',tempo_bpm:100,meter_beats:4,notes:'',parts:[blankPart()],partner_ids:[] as string[]})
  const [history,setHistory]=useState<Practice[]>([]),[tracks,setTracks]=useState<Track[]>([]),[chosen,setChosen]=useState<string[]>([])
  const [grade,setGrade]=useState(3),[practiceNotes,setPracticeNotes]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[dirty,setDirty]=useState(false),[offset,setOffset]=useState(0)
  async function request(path:string,method='GET',data?:unknown){const response=await fetch(apiBase+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw new Error(value.message||value.error);return value}
  function show(value:Round){setItem(value);setDraft({title:value.title,tempo_bpm:value.tempo_bpm,meter_beats:value.meter_beats,notes:value.notes,parts:value.parts,partner_ids:value.partner_ids});setChosen(value.parts.map(part=>part.id));setDirty(false)}
  function open(value:string){window.location.hash=`#/capabilities/music/rounds${value?'/'+value:''}`;setId(value)}
  useEffect(()=>{const changed=()=>setId(selected());window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed)},[])
  useEffect(()=>{let active=true;fetch(catalogBase+'/tracks?limit=100').then(response=>response.json()).then(value=>{if(active&&Array.isArray(value.items))setTracks(value.items)}).catch(()=>{});return()=>{active=false}},[catalogBase])
  useEffect(()=>{if(id&&item?.id===id)return;let active=true;setLoading(true);setItem(null);setError('');request(id?'/'+id:`?offset=${offset}&limit=25`).then(value=>{if(!active)return;if(id)show(value.item);else{setRows(value.items);setDraft({title:'',tempo_bpm:100,meter_beats:4,notes:'',parts:[blankPart()],partner_ids:[]});setDirty(false)}}).catch(err=>{if(active)setError(err.message)}).finally(()=>{if(active)setLoading(false)});if(id)request('/'+id+'/practice').then(value=>{if(active)setHistory(value.items)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[id,offset,apiBase])
  function edit(changes:Partial<typeof draft>){setDraft(value=>({...value,...changes}));setDirty(true)}
  function editPart(index:number,changes:Partial<Part>){edit({parts:draft.parts.map((part,i)=>i===index?{...part,...changes}:part)})}
  async function save(){setBusy(true);setError('');try{const value=await request(id?'/'+id:'',id?'PATCH':'POST',{...draft,...(item?{revision:item.revision}:{})});show(value.item);if(!id)open(value.item.id)}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
  async function practice(){if(!item)return;setBusy(true);setError('');try{const value=await request('/'+id+'/practice','POST',{request_id:crypto.randomUUID(),round_revision:item.revision,part_ids:chosen,occurred_at:new Date().toISOString(),grade,notes:practiceNotes});setHistory(previous=>[...previous.filter(row=>row.id!==value.item.id),value.item]);setPracticeNotes('')}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
  return <ListScaffold title="Musical canons and part practice" bodyClassName="mx-auto flex w-full max-w-4xl flex-col gap-l px-l py-xl">
    {error&&<p role="alert">{error}</p>}
    {loading?<p role="status">Loading canons…</p>:<>
      {!id&&<><ul>{rows.map(row=><li key={row.id}><a className="text-primary" href={`#/capabilities/music/rounds/${row.id}`} onClick={()=>setId(row.id)}>{row.title}</a> · {row.parts.length} parts · {row.id}</li>)}</ul>{rows.length===0&&<p>No canons yet.</p>}<div className="flex gap-2"><Button disabled={!offset} onClick={()=>setOffset(value=>Math.max(0,value-25))}>Previous</Button><Button disabled={rows.length<25} onClick={()=>setOffset(value=>value+25)}>Next</Button></div></>}
      {id&&<Button variant="secondary" onClick={()=>open('')}>All canons</Button>}
      {(!id||item)&&<form className="flex flex-col gap-3" onSubmit={event=>{event.preventDefault();void save()}}>
        <label>Canon title<input className={cls} required maxLength={200} value={draft.title} onChange={event=>edit({title:event.target.value})}/></label>
        <label>Tempo BPM<input className={cls} type="number" min={20} max={300} value={draft.tempo_bpm} onChange={event=>edit({tempo_bpm:Number(event.target.value)})}/></label>
        <label>Beats per measure<input className={cls} type="number" min={1} max={16} value={draft.meter_beats} onChange={event=>edit({meter_beats:Number(event.target.value)})}/></label>
        <label>Arrangement notes<textarea className={`${cls} h-auto min-h-24 py-2`} value={draft.notes} onChange={event=>edit({notes:event.target.value})}/></label>
        <label>Partner canon IDs (one per line)<textarea className={`${cls} h-auto min-h-24 py-2`} value={draft.partner_ids.join('\n')} onChange={event=>edit({partner_ids:event.target.value.split('\n').map(value=>value.trim()).filter(Boolean)})}/></label>
        {draft.parts.map((part,index)=><fieldset className="rounded-lg border border-outline p-3" key={part.id}><legend>Part {index+1}</legend>
          <label>Part {index+1} name<input className={cls} value={part.name} onChange={event=>editPart(index,{name:event.target.value})}/></label>
          <label>Part {index+1} entry beat<input className={cls} type="number" min={0} step="any" value={part.entry_beats} onChange={event=>editPart(index,{entry_beats:Number(event.target.value)})}/></label>
          <p>Entry at {(part.entry_beats*60/draft.tempo_bpm).toFixed(2)} seconds</p>
          <label>Part {index+1} notation<textarea className={`${cls} h-auto min-h-24 py-2`} value={part.notation} onChange={event=>editPart(index,{notation:event.target.value})}/></label>
          <label>Part {index+1} recording<select className={cls} value={part.catalog_ref?part.catalog_ref.track_id+':'+part.catalog_ref.render_id:''} onChange={event=>{const [track_id,render_id]=event.target.value.split(':');editPart(index,{catalog_ref:track_id?{track_id,render_id}:null})}}><option value="">No recording attached</option>{tracks.flatMap(track=>track.renders.filter(render=>render.source.kind==='imported').map(render=><option key={render.id} value={track.id+':'+render.id}>{track.title} · {render.artifact_ref.slug}</option>))}</select></label>
          <Button variant="secondary" disabled={draft.parts.length<=1} onClick={()=>edit({parts:draft.parts.filter((_,i)=>i!==index)})}>Remove part {index+1}</Button>
        </fieldset>)}
        <Button variant="secondary" disabled={draft.parts.length>=16} onClick={()=>edit({parts:[...draft.parts,blankPart()]})}>Add voice part</Button>
        <Button type="submit" disabled={busy||!draft.title.trim()}>{item?'Save arrangement':'Create canon'}</Button>
      </form>}
      {item&&<article aria-label="Part practice" className="flex flex-col gap-3"><h2>{item.title}</h2><p>Practice uses saved arrangement revision {item.revision}.</p>
        {item.parts.map(part=>{const track=tracks.find(row=>row.id===part.catalog_ref?.track_id);const ref=track?.renders.find(row=>row.id===part.catalog_ref?.render_id)?.artifact_ref;return <div key={part.id}><label className="flex items-center gap-s"><Checkbox ariaLabel={part.name} checked={chosen.includes(part.id)} onChange={checked=>setChosen(values=>checked?[...values,part.id]:values.filter(value=>value!==part.id))}/>{part.name}</label><pre className="whitespace-pre-wrap font-sans">{part.notation}</pre>{ref?<audio aria-label={`Play ${part.name}`} controls src={`/api/artifacts/${ref.slug}/raw?version=${ref.version}`}/>:<p>{part.catalog_ref?'Recording unavailable.':'No recording attached.'}</p>}</div>})}
        <label>Practice grade<select className={cls} value={grade} onChange={event=>setGrade(Number(event.target.value))}>{[0,1,2,3,4,5].map(value=><option key={value}>{value}</option>)}</select></label>
        <label>Practice notes<textarea className={`${cls} h-auto min-h-24 py-2`} value={practiceNotes} onChange={event=>setPracticeNotes(event.target.value)}/></label>
        <Button disabled={busy||dirty||chosen.length===0} onClick={()=>void practice()}>Log part practice</Button>{dirty&&<p>Save arrangement changes before practicing.</p>}
        <ol aria-label="Part practice history">{history.map(row=><li key={row.id}>Grade {row.grade} · {row.part_ids.length} parts · revision {row.round_revision} · {row.notes}</li>)}</ol>
      </article>}
    </>}
  </ListScaffold>
}
