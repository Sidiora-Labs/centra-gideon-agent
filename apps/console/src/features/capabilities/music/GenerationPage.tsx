import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
type Config = { enabled: boolean; model: string; credential_name: string; revision: number }
type Job = { id: string; status: string; error: string | null; artifact_ref: {slug:string;version:number} | null }
const cls = 'w-full rounded-lg border border-outline bg-surface p-2 text-on-surface'
export default function GenerationPage({ apiBase = '/api/capabilities/music/generation' }: { apiBase?: string }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [ready, setReady] = useState(false)
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [track, setTrack] = useState('')
  const [revision, setRevision] = useState(1)
  const [prompt, setPrompt] = useState('')
  const [duration, setDuration] = useState('')
  const [instrumental, setInstrumental] = useState(false)
  const [license, setLicense] = useState('')
  async function request(path: string, method='GET', data?: unknown) {
    const response = await fetch(apiBase+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)})
    const value=await response.json(); if(!response.ok) throw new Error(value.message||value.error); return value
  }
  useEffect(()=>{let active=true;request('/readiness').then(value=>{if(active){setConfig(value.config);setReady(value.ready_to_submit)}}).catch(err=>{if(active)setError(err.message)});request('/jobs').then(value=>{if(active)setJobs(value.jobs)}).catch(err=>{if(active)setError(err.message)});return()=>{active=false}},[apiBase])
  async function act(path:string,method:string,data?:unknown){setBusy(true);setError('');try{const value=await request(path,method,data);if(value.config){setConfig(value.config);setReady(false)}if(value.job)setJobs(previous=>[value.job,...previous.filter(job=>job.id!==value.job.id)])}catch(err){setError((err as Error).message)}finally{setBusy(false)}}
  return <section className="mx-auto flex w-full max-w-4xl flex-col gap-4 overflow-auto p-4 text-on-surface">
    <h1>Music generation</h1><a href="#/capabilities/music/catalog/tracks" className="text-primary">Music catalog</a>
    <p>ElevenLabs composition. Submitting uses your configured provider account. Remote availability and account entitlement remain unverified until an actual request.</p>
    {error&&<p role="alert">{error}</p>}
    {!config?<p role="status">Loading engine…</p>:<>
      <label><input type="checkbox" checked={config.enabled} onChange={event=>{setReady(false);setConfig({...config,enabled:event.target.checked})}}/> Enable music engine</label>
      <label>Named credential<input className={cls} value={config.credential_name} onChange={event=>{setReady(false);setConfig({...config,credential_name:event.target.value})}}/></label>
      <label>Model<select className={cls} value={config.model} onChange={event=>{setReady(false);setConfig({...config,model:event.target.value})}}>{['music_v1','music_v2','music_v2_5'].map(model=><option key={model}>{model}</option>)}</select></label>
      <Button disabled={busy} onClick={()=>void act('/config','PATCH',config)}>Save engine settings</Button>
      <Button variant="secondary" disabled={busy} onClick={()=>{void request('/readiness').then(value=>{setConfig(value.config);setReady(value.ready_to_submit)}).catch(err=>setError(err.message))}}>Refresh readiness</Button>
      <p>{ready?'Local credential available; remote generation unverified.':'Engine disabled or named credential unavailable.'}</p>
      <label>Track ID<input className={cls} value={track} onChange={event=>setTrack(event.target.value)}/></label>
      <label>Track revision<input className={cls} type="number" min={1} value={revision} onChange={event=>setRevision(Number(event.target.value))}/></label>
      <label>Composition prompt<textarea className={cls} maxLength={4100} value={prompt} onChange={event=>setPrompt(event.target.value)}/></label>
      <label>Duration milliseconds (empty for automatic)<input className={cls} type="number" min={3000} max={600000} value={duration} onChange={event=>setDuration(event.target.value)}/></label>
      <label><input type="checkbox" checked={instrumental} onChange={event=>setInstrumental(event.target.checked)}/> Instrumental only</label>
      <label>License or rights statement<input className={cls} value={license} onChange={event=>setLicense(event.target.value)}/></label>
      <Button disabled={busy||!ready||!track||!prompt||!license} onClick={()=>void act('/jobs','POST',{request_id:crypto.randomUUID(),track_id:track,track_revision:revision,prompt,music_length_ms:duration?Number(duration):null,force_instrumental:instrumental,license})}>Compose music</Button>
      <ul aria-label="Generation jobs">{jobs.map(job=><li className="rounded-lg border border-outline p-3" key={job.id}><p>{job.id}: {job.status}</p>{job.error&&<p>{job.error}</p>}{job.artifact_ref&&<a href={`/api/artifacts/${job.artifact_ref.slug}/raw?version=${job.artifact_ref.version}`}>Provider audio</a>}<Button variant="secondary" onClick={()=>void act(`/jobs/${encodeURIComponent(job.id)}`,'GET')}>Refresh job</Button>{['queued','running'].includes(job.status)&&<Button disabled={busy} onClick={()=>void act(`/jobs/${encodeURIComponent(job.id)}/cancel`,'POST',{})}>Cancel local request</Button>}</li>)}</ul>
    </>}
  </section>
}
