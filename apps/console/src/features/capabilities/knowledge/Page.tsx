import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Entry = { source_type: string; source_id: string; original_at: string; title: string; excerpt: string; source_link: string; years_ago: number }
type Result = { date: string; items: Entry[]; total: number; next_offset: number | null; skipped_invalid_dates: number; sources: { memory: string } }

export default function Page() {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const changed = () => setHash(window.location.hash)
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  const initial = new URLSearchParams(hash.split('?')[1] || '')
  const [timezone, setTimezone] = useState(initial.get('timezone') || Intl.DateTimeFormat().resolvedOptions().timeZone)
  const [date, setDate] = useState(initial.get('date') || new Date().toLocaleDateString('en-CA'))
  const [offset, setOffset] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const [data, setData] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [memory, setMemory] = useState<{ title: string; content: string } | null>(null)
  const memoryId = initial.get('memory')
  useEffect(() => {
    let active = true
    setData(null); setError(''); setMemory(null)
    const params = new URLSearchParams({ date, timezone, offset: String(offset), limit: '20' })
    const url = memoryId ? `/api/capabilities/knowledge/sources/memory/${encodeURIComponent(memoryId)}` : `/api/capabilities/knowledge/anniversaries?${params}`
    requestJson<Result & { title: string; content: string }>(url).then(result => {
      if (active) { if (memoryId) setMemory(result); else setData(result) }
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Could not load anniversaries.') })
    return () => { active = false }
  }, [date, timezone, offset, attempt, memoryId])
  const remember = (nextDate: string, nextZone: string) => {
    setDate(nextDate); setTimezone(nextZone); setOffset(0)
    window.history.replaceState(null, '', `#/capabilities/knowledge?${new URLSearchParams({ date: nextDate, timezone: nextZone })}`)
    setHash(window.location.hash)
  }
  return <main className="mx-auto flex w-full max-w-4xl flex-col gap-l p-l">
    <h1 className="text-2xl font-semibold">On this day</h1>
    <p>Revisit your notes, journals and memories from previous years. February 29 appears only on February 29.</p>
    <div className="flex flex-wrap gap-m">
      <label className="flex flex-col gap-xs">Date<input className="rounded border border-outline-variant bg-surface-container p-s" type="date" value={date} onChange={event => remember(event.target.value, timezone)} /></label>
      <label className="flex min-w-0 flex-col gap-xs">Timezone<input className="rounded border border-outline-variant bg-surface-container p-s" value={timezone} onChange={event => remember(date, event.target.value)} /></label>
    </div>
    {error ? <div role="alert"><p>{error}</p><Button onClick={() => setAttempt(attempt + 1)}>Retry</Button></div> : !data && !memory ? <p role="status">Loading anniversaries…</p> : null}
    {memory ? <article><h2>{memory.title}</h2><p className="whitespace-pre-wrap break-words">{memory.content}</p><a href="#/capabilities/knowledge">Back to anniversaries</a></article> : null}
    {data ? <>
      <p role="status">{data.total ? `${data.total} memories of this date` : 'No anniversaries on this date.'}</p>
      {data.sources.memory !== 'available' ? <p>Memory history is not connected. Showing your knowledge library.</p> : null}
      {data.skipped_invalid_dates ? <p>{data.skipped_invalid_dates} records have an unreadable original date.</p> : null}
      <ul className="flex flex-col gap-m">{data.items.map(item => <li key={`${item.source_type}:${item.source_id}`} className="rounded border border-outline-variant p-m">
        <p>{item.years_ago} {item.years_ago === 1 ? 'year' : 'years'} ago · {item.source_type}</p>
        <h2 className="font-semibold"><a className="text-primary underline" href={item.source_link}>{item.title || 'Untitled'}</a></h2>
        <time>{item.original_at}</time><p className="break-words">{item.excerpt}</p>
      </li>)}</ul>
      <div className="flex gap-m"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><Button disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset ?? offset)}>Next</Button></div>
    </> : null}
  </main>
}
