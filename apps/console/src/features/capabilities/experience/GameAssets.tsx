import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'
import { StatusPill } from '../../../shared/ui/StatusPill'

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
  return <section aria-label="Game asset compiler" className="space-y-m">
    <header className="space-y-xs"><h2 data-type="title-m">Game asset compiler</h2><p className="text-on-surface-variant">Exports use exact artifact versions and publish only after managed-app destination verification.</p></header>
    {error&&<p role="alert">{error}</p>}<p role="status">{projects.length} game projects</p>
    <ul className="grid gap-m xl:grid-cols-2">{projects.map(project=>{const count=Object.keys(project.bindings).length;return <li key={project.id}><Surface className="flex h-full flex-col gap-m p-m"><div className="flex items-start justify-between gap-s"><div><h3 data-type="title-m">{project.title}</h3><p className="text-on-surface-variant">{project.app_id}</p></div><StatusPill tone={project.publication?'ok':project.compiled?'info':'neutral'}>{project.publication?'published '+project.publication.state:project.compiled?'compiled v'+project.compiled.version:'not compiled'}</StatusPill></div><p>{count}/4 assets{project.publication&&` · ${project.publication.destination}`}</p><div className="mt-auto flex flex-wrap gap-s"><Button disabled={busy===project.id||count!==4} onClick={()=>void action(project,'compile')}>Compile runnable export</Button><Button variant="secondary" disabled={busy===project.id||!project.compiled} onClick={()=>void action(project,'publish')}>Publish to managed app</Button></div></Surface></li>})}</ul>
    <Button variant="secondary" disabled={Boolean(busy)} onClick={()=>void refresh().catch(error=>setError(String(error)))}>Refresh projects</Button>
  </section>
}
