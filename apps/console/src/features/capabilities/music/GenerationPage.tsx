import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import {ListScaffold} from '../../../shared/ui/ListScaffold'
import { Checkbox, TextInput, Select, TextArea } from '../../../shared/ui/forms'
import { Button } from '../../../shared/ui/Button'
type Config = { enabled: boolean; model: string; credential_name: string; revision: number }
type Job = { id: string; status: string; error: string | null; artifact_ref: {slug:string;version:number} | null }
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
  return <ListScaffold title="Music generation" bodyClassName="mx-auto flex w-full max-w-4xl flex-col gap-l px-l py-xl">

    <p>ElevenLabs composition. Submitting uses your configured provider account. Remote availability and account entitlement remain unverified until an actual request.</p>
    {error&&<p role="alert">{error}</p>}
    {!config?<p role="status">Loading engine…</p>:<>
      <label className="flex items-center gap-s"><Checkbox ariaLabel="Enable music engine" checked={config.enabled} onChange={enabled=>{setReady(false);setConfig({...config,enabled})}}/>Enable music engine</label>
      <label>Named credential<TextInput value={config.credential_name} onChange={(nextValue) => { setReady(false); setConfig({ ...config, credential_name: nextValue }); }}/></label>
      <label>Model<Select value={String(config.model)} onChange={(nextValue) => { setReady(false); setConfig({ ...config, model: nextValue }); }} options={[...['music_v1', 'music_v2', 'music_v2_5'].map(model => ({ value: String(model), label: String(model) }))]}/></label>
      <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={()=>void act('/config','PATCH',config)}>Save engine settings</Button>
      <Button variant="secondary" disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={()=>{void request('/readiness').then(value=>{setConfig(value.config);setReady(value.ready_to_submit)}).catch(err=>setError(err.message))}}>Refresh readiness</Button>
      <p>{ready?'Local credential available; remote generation unverified.':'Engine disabled or named credential unavailable.'}</p>
      <label>Track ID<TextInput value={track} onChange={(nextValue) => setTrack(nextValue)}/></label>
      <label>Track revision<TextInput type="number" min={1} value={String(revision)} onChange={(nextValue) => setRevision(Number(nextValue))}/></label>
      <label>Composition prompt<TextArea maxLength={4100} value={prompt} onChange={(nextValue) => setPrompt(nextValue)} className="min-h-24"/></label>
      <label>Duration milliseconds (empty for automatic)<TextInput type="number" min={3000} max={600000} value={String(duration)} onChange={(nextValue) => setDuration(nextValue)}/></label>
      <label className="flex items-center gap-s"><Checkbox ariaLabel="Instrumental only" checked={instrumental} onChange={setInstrumental}/>Instrumental only</label>
      <label>License or rights statement<TextInput value={license} onChange={(nextValue) => setLicense(nextValue)}/></label>
      <Button disabled={busy||!ready||!track||!prompt||!license} disabledReason={busy ? BUSY_REASON : !ready ? 'Enable the engine with an available named credential before composing.' : !track ? 'Choose a track before composing.' : !prompt ? 'Enter a composition prompt.' : !license ? 'Enter a license or rights statement before composing.' : undefined} onClick={()=>void act('/jobs','POST',{request_id:crypto.randomUUID(),track_id:track,track_revision:revision,prompt,music_length_ms:duration?Number(duration):null,force_instrumental:instrumental,license})}>Compose music</Button>
      <ul aria-label="Generation jobs">{jobs.map((job,index)=><li className="rounded-lg border border-outline p-3" key={job.id}><p>{job.id}: {job.status}</p>{job.error&&<p>{job.error}</p>}{job.artifact_ref&&<a href={`/api/artifacts/${job.artifact_ref.slug}/raw?version=${job.artifact_ref.version}`}>Provider audio</a>}<Button ariaLabel={`Refresh composition job ${index + 1} (${job.status})`} variant="secondary" onClick={()=>void act(`/jobs/${encodeURIComponent(job.id)}`,'GET')}>Refresh job</Button>{['queued','running'].includes(job.status)&&<Button ariaLabel={`Cancel local composition request ${index + 1} (${job.status})`} disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={()=>void act(`/jobs/${encodeURIComponent(job.id)}/cancel`,'POST',{})}>Cancel local request</Button>}</li>)}</ul>
    </>}
  </ListScaffold>
}
