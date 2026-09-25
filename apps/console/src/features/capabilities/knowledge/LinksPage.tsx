import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Link = { id: string; title: string; url: string }
type Bucket = { id: string; name: string; icon: string; links: Link[] }
type Repo = { id: string; url: string; status: string; stage: string; revision: string; report_id?: string; error: string; events: { sequence: number; detail: string; status: string }[] }
const root = '/api/capabilities/knowledge/links'

export default function LinksPage() {
  const [buckets, setBuckets] = useState<Bucket[]>([]), [selected, setSelected] = useState<Bucket | null>(null), [repos, setRepos] = useState<Repo[]>([]), [repo, setRepo] = useState<Repo | null>(null)
  const [bucketName, setBucketName] = useState(''), [title, setTitle] = useState(''), [url, setUrl] = useState(''), [repoUrl, setRepoUrl] = useState('')
  const [report, setReport] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const requestId = useRef(crypto.randomUUID())
  async function load() { const [bucketData, repoData] = await Promise.all([requestJson<{ buckets: Bucket[] }>(root + '/buckets'), requestJson<{ items: Repo[] }>(root + '/repositories')]); setBuckets(bucketData.buckets); setRepos(repoData.items); setSelected(current => current ? bucketData.buckets.find(item => item.id === current.id) ?? null : current); setRepo(current => current ? repoData.items.find(item => item.id === current.id) ?? current : current) }
  useEffect(() => { load().catch(e => setError(e.message)) }, [])
  async function act(fn: () => Promise<void>) { setBusy(true); setError(''); try { await fn() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function createBucket() { await act(async () => { const bucket = await requestJson<Bucket>(root + '/buckets', 'POST', { name: bucketName, icon: '🔗' }); setBucketName(''); await load(); setSelected(bucket) }) }
  async function addLink() { if (!selected) return; await act(async () => { await requestJson(root + '/links', 'POST', { url, title, bucket_id: selected.id }); setUrl(''); setTitle(''); await load() }) }
  async function moveLink(index: number, direction: number) { if (!selected) return; const ids = selected.links.map(item => item.id); const target = index + direction; if (target < 0 || target >= ids.length) return; [ids[index], ids[target]] = [ids[target], ids[index]]; await act(async () => { await requestJson(`${root}/buckets/${selected.id}/links-reorder`, 'POST', { ids }); await load() }) }
  async function deleteBucket() { if (!selected) return; await act(async () => { await requestJson(`${root}/buckets/${selected.id}/bucket`, 'DELETE'); setSelected(null); await load() }) }
  async function intake() { await act(async () => { const created = await requestJson<Repo>(root + '/repositories', 'POST', { request_id: requestId.current, url: repoUrl }); setRepo(created); requestId.current = crypto.randomUUID(); await load() }) }
  async function restudy() { if (!repo) return; await act(async () => { setRepo(await requestJson<Repo>(`${root}/repositories/${repo.id}/study`, 'POST')); await load() }) }
  async function readReport() { if (!repo) return; await act(async () => setReport((await requestJson<{ content: string }>(`${root}/repositories/${repo.id}/report`)).content)) }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Links and repository study</h1><p>Keep ordered bookmark buckets and study guarded public repository snapshots in canonical Knowledge.</p>{error && <p role="alert">{error}</p>}
    <section aria-label="Link buckets"><h2>Link buckets</h2><label>Bucket name<input aria-label="Bucket name" value={bucketName} onChange={e => setBucketName(e.target.value)} /></label><Button disabled={busy || !bucketName.trim()} onClick={() => void createBucket()}>Create bucket</Button><nav aria-label="Buckets">{buckets.map(item => <Button key={item.id} disabled={busy} onClick={() => setSelected(item)}>{item.icon} {item.name} · {item.links.length}</Button>)}</nav></section>
    {selected && <section aria-label="Selected bucket"><h2>{selected.name}</h2><label>Link title<input aria-label="Link title" value={title} onChange={e => setTitle(e.target.value)} /></label><label>Link URL<input aria-label="Link URL" value={url} onChange={e => setUrl(e.target.value)} /></label><Button disabled={busy || !title || !url} onClick={() => void addLink()}>Add link</Button><Button disabled={busy} onClick={() => void deleteBucket()}>Delete bucket, keep links</Button><ol>{selected.links.map((item, index) => <li key={item.id}><a href={item.url}>{item.title}</a><Button disabled={busy || index === 0} onClick={() => void moveLink(index, -1)}>Move {item.title} up</Button><Button disabled={busy || index === selected.links.length - 1} onClick={() => void moveLink(index, 1)}>Move {item.title} down</Button></li>)}</ol></section>}
    <section aria-label="Repository intake"><h2>Repository intake</h2><label>Public repository URL<input aria-label="Public repository URL" value={repoUrl} onChange={e => { setRepoUrl(e.target.value); requestId.current = crypto.randomUUID() }} /></label><Button disabled={busy || !repoUrl} onClick={() => void intake()}>Fetch and study repository</Button><nav aria-label="Repository studies">{repos.map(item => <Button key={item.id} disabled={busy} onClick={() => setRepo(item)}>{item.url.split('/').pop()} · {item.status}</Button>)}</nav></section>
    {repo && <section aria-label="Selected repository"><h2>{repo.url}</h2><p>Status: {repo.status} · {repo.stage}</p>{repo.revision && <p>Revision: {repo.revision}</p>}{repo.error && <p role="alert">{repo.error}</p>}<Button disabled={busy || !repo.revision} onClick={() => void restudy()}>Refresh study</Button><Button disabled={busy || !repo.report_id} onClick={() => void readReport()}>Read study report</Button><ul>{repo.events.map(event => <li key={event.sequence}>{event.status} · {event.detail}</li>)}</ul></section>}
    {report && <section aria-label="Repository report"><pre className="whitespace-pre-wrap">{report}</pre></section>}
  </main>
}
