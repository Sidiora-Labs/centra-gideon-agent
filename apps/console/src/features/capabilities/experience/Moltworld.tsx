import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Readiness={protocol:string;base_url:string;config:{enabled:boolean;credential_name:string;revision:number};credential_available:boolean;remote_status:string;ready:boolean}
type Receipt={request_id:string;action:string;state:string;approval:string;queued_for_tick?:number;error?:string}
type Snapshot={readiness:Readiness;history:Receipt[]}

export default function Moltworld({baseUrl='/api/capabilities/experience'}:{baseUrl?:string}) {
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[remote,setRemote]=useState<Record<string,unknown>|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false)
  const refresh=async()=>setSnapshot(await requestJson<Snapshot>(baseUrl+'/moltworld'))
  useEffect(()=>{void refresh().catch(error=>setError(String(error)))},[baseUrl])
  async function status(){setBusy(true);setError('');try{setRemote(await requestJson(baseUrl+'/moltworld/status'))}catch(error){setError(String(error))}finally{setBusy(false)}}
  return <section aria-label="Moltworld adapter" className="space-y-3 rounded-lg border p-4">
    <h2>Moltworld</h2><p>External actions require approval. A queued receipt is not proof that the tick completed.</p>
    {error&&<p role="alert">{error}</p>}{!snapshot?<p>Loading Moltworld…</p>:<>
      <p role="status">{snapshot.readiness.ready?'ready locally':'not ready'} · remote {snapshot.readiness.remote_status} · {snapshot.readiness.protocol}</p>
      <p>Credential: {snapshot.readiness.config.credential_name||'not configured'} · secret {snapshot.readiness.credential_available?'available':'unavailable'}</p>
      <Button disabled={busy||!snapshot.readiness.ready} onClick={()=>void status()}>Verify remote status</Button>
      {remote&&<pre aria-label="Verified remote agent state">{JSON.stringify(remote,null,2)}</pre>}
      <h3>Action history</h3><ul>{snapshot.history.map(row=><li key={row.request_id}><strong>{row.action}</strong> · {row.state} · {row.approval}{row.queued_for_tick!==undefined&&' · tick '+row.queued_for_tick}{row.error&&' · '+row.error}</li>)}</ul>
      <Button disabled={busy} onClick={()=>void refresh().catch(error=>setError(String(error)))}>Refresh local history</Button>
    </>}
  </section>
}
