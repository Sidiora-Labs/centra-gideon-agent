import { useEffect, useRef, useState } from 'react'
import { gatewayHeaders, readJson, requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Topic = { id: string; name: string; query: string; source_types: string[]; revision: number }
type Matches = { items: { source_type: string; source_id: string; title: string; excerpt: string; source_link: string; matched_terms: string[] }[]; sources: Record<string, string>; truncated: string[]; total: number; total_is_complete: boolean; next_offset: number | null }
const root = '/api/capabilities/knowledge/topics'
const kinds = ['note', 'journal', 'fleeting', 'memory', 'person', 'project', 'task']

export default function TopicsPage() {
  const [topics, setTopics] = useState<Topic[]>([])
  const [selected, setSelected] = useState<Topic | null>(null)
  const [name, setName] = useState('')
  const [query, setQuery] = useState('')
  const [sources, setSources] = useState<string[]>(['note', 'journal', 'fleeting'])
  const [matches, setMatches] = useState<Matches | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [offset, setOffset] = useState(0)
  const [topicOffset, setTopicOffset] = useState(0)
  const [topicNext, setTopicNext] = useState<number | null>(null)
  const [reload, setReload] = useState(0)
  const requestId = useRef(crypto.randomUUID())
  const deleteId = useRef(crypto.randomUUID())
  useEffect(() => {
    let active = true
    requestJson<{ items: Topic[]; next_offset: number | null }>(`${root}?offset=${topicOffset}`).then(data => { if (active) { setTopics(data.items); setTopicNext(data.next_offset) } }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [topicOffset])
  useEffect(() => {
    let active = true
    setMatches(null)
    if (selected) requestJson<Matches>(`${root}/${selected.id}/matches?offset=${offset}`).then(data => { if (active) setMatches(data) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [selected?.id, selected?.revision, offset, reload])
  function choose(topic: Topic | null) { setSelected(topic); setName(topic?.name || ''); setQuery(topic?.query || ''); setSources(topic?.source_types || ['note', 'journal', 'fleeting']); setOffset(0); setError(''); requestId.current = crypto.randomUUID(); deleteId.current = crypto.randomUUID() }
  function edit() { requestId.current = crypto.randomUUID(); setError('') }
  async function save() {
    setBusy(true); setError('')
    try {
      const topic = await requestJson<Topic>(root, 'POST', { request_id: requestId.current, name, query, source_types: sources, ...(selected ? { id: selected.id, revision: selected.revision } : {}) })
      setTopics(current => [topic, ...current.filter(item => item.id !== topic.id)]); setSelected(topic); setOffset(0); requestId.current = crypto.randomUUID(); deleteId.current = crypto.randomUUID()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function remove() {
    if (!selected) return
    setBusy(true); setError('')
    try {
      await readJson(await fetch(`${root}/${selected.id}`, { method: 'DELETE', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: deleteId.current, revision: selected.revision }) }))
      setTopics(current => current.filter(item => item.id !== selected.id)); choose(null)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Tracked topics</h1><p>Save keyword queries across your current records. Every query word must match; up to 1000 records per source are scanned.</p>
    {error && <p role="alert">{error}</p>}
    <nav aria-label="Saved topics">{topics.map(topic => <Button key={topic.id} disabled={busy} onClick={() => choose(topic)}>{topic.name}</Button>)}<Button disabled={busy} onClick={() => choose(null)}>New topic</Button><Button disabled={busy || topicOffset === 0} onClick={() => setTopicOffset(Math.max(0, topicOffset - 20))}>Previous topics</Button><Button disabled={busy || topicNext === null} onClick={() => setTopicOffset(topicNext!)}>Next topics</Button></nav>
    <label className="block">Topic name<input aria-label="Topic name" value={name} disabled={busy} onChange={e => { setName(e.target.value); edit() }} /></label>
    <label className="block">Keyword query<input aria-label="Keyword query" value={query} disabled={busy} onChange={e => { setQuery(e.target.value); edit() }} /></label>
    <fieldset><legend>Sources</legend>{kinds.map(kind => <label key={kind} className="mr-4"><input type="checkbox" checked={sources.includes(kind)} disabled={busy} aria-label={kind} onChange={e => { setSources(current => e.target.checked ? [...current, kind] : current.filter(value => value !== kind)); edit() }} />{kind}</label>)}</fieldset>
    <Button disabled={busy || !name.trim() || !query.trim() || !sources.length} onClick={() => void save()}>Save topic</Button>{selected && <Button disabled={busy} onClick={() => void remove()}>Delete topic</Button>}
    {selected && <section aria-label="Topic evidence"><h2>{selected.name}</h2><Button disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh sources</Button>{matches && <><p>{matches.total} matches{!matches.total_is_complete && ' in the available scanned records'}</p>{Object.entries(matches.sources).filter(([, state]) => state === 'unavailable').map(([kind]) => <p role="status" key={kind}>{kind} source unavailable</p>)}{matches.truncated.length > 0 && <p role="status">Scan limit reached: {matches.truncated.join(', ')}</p>}{matches.items.map(item => <article className="my-3 border p-3" key={item.source_type + item.source_id}><a href={item.source_link}>{item.title}</a><p>{item.source_type} · matched {item.matched_terms.join(', ')}</p><p className="whitespace-pre-wrap">{item.excerpt}</p></article>)}<Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous matches</Button><Button disabled={matches.next_offset === null} onClick={() => setOffset(matches.next_offset!)}>Next matches</Button></>}</section>}
  </main>
}
