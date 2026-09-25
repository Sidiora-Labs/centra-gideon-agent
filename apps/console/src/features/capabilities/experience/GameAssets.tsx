import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Project = { id:string; revision:number; title:string; app_id:string; foundation_id:string; bindings:Record<string,unknown>; compiled:null|{version:number;asset_count:number}; publication:null|{state:string;destination:string} }

export default function GameAssets({ baseUrl='/api/capabilities/experience' }: { baseUrl?:string }) {
  const [projects,setProjects] = useState<Project[]>([]), [error,setError] = useState(''), [busy,setBusy] = useState('')
  const refresh = async () => setProjects((await requestJson<{projects:Project[]}>(baseUrl+'/game-assets/projects')).projects)
  useEffect(()=>{ void refresh().catch(error=>setError(String(error))) },[baseUrl])
  async function action(project:Project, operation:'compile'|'publish') {
    setBusy(project.id); setError('')
    try { await requestJson(baseUrl+'/game-assets/projects/'+project.id+'/'+operation,'POST',{revision:project.revision}); await refresh() }
    catch(error) { setError(String(error)) } finally { setBusy('') }
  }
  return <section aria-label="Game asset compiler" className="space-y-3 rounded-lg border p-4">
    <h2>Game asset compiler</h2><p>Exports use exact artifact versions and publish only after managed-app destination verification.</p>
    {error&&<p role="alert">{error}</p>}<p role="status">{projects.length} game projects</p>
    <ul>{projects.map(project=><li key={project.id}><strong>{project.title}</strong> · {project.app_id} · {Object.keys(project.bindings).length}/4 assets · {project.compiled?'compiled v'+project.compiled.version:'not compiled'} · {project.publication?'published '+project.publication.state:'not published'} <Button disabled={busy===project.id||Object.keys(project.bindings).length!==4} onClick={()=>void action(project,'compile')}>Compile runnable export</Button><Button disabled={busy===project.id||!project.compiled} onClick={()=>void action(project,'publish')}>Publish to managed app</Button>{project.publication&&<span> · {project.publication.destination}</span>}</li>)}</ul>
    <Button disabled={Boolean(busy)} onClick={()=>void refresh().catch(error=>setError(String(error)))}>Refresh projects</Button>
  </section>
}
