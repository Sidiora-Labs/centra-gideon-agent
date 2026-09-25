import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { DateInput, Field, TextInput } from '../../../shared/ui/forms'

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
  return <main className="h-full"><ListScaffold title="On this day" bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl">
    <p data-type="body-m" className="max-w-[42rem] text-on-surface-var">Revisit your notes, journals and memories from previous years. February 29 appears only on February 29.</p>
    <div className="flex flex-wrap items-end gap-m rounded-lg bg-surface-container p-l">
      <Field label="Date"><DateInput value={date} onChange={value => remember(value, timezone)} /></Field>
      <div className="min-w-56 flex-1"><Field label="Timezone"><TextInput value={timezone} onChange={value => remember(date, value)} surface="high" /></Field></div>
    </div>
    {error ? <div role="alert"><p>{error}</p><Button onClick={() => setAttempt(attempt + 1)}>Retry</Button></div> : !data && !memory ? <p role="status">Loading anniversaries…</p> : null}
    {memory ? <article className="rounded-lg bg-surface-container p-l"><h2 data-type="title-m" className="mb-m text-on-surface">{memory.title}</h2><p className="whitespace-pre-wrap break-words">{memory.content}</p><a className="mt-m inline-block text-primary underline" href="#/capabilities/knowledge">Back to anniversaries</a></article> : null}
    {data ? <>
      <p role="status">{data.total ? `${data.total} memories of this date` : 'No anniversaries on this date.'}</p>
      {data.sources.memory !== 'available' ? <p>Memory history is not connected. Showing your knowledge library.</p> : null}
      {data.skipped_invalid_dates ? <p>{data.skipped_invalid_dates} records have an unreadable original date.</p> : null}
      <ul className="flex flex-col gap-s">{data.items.map(item => <li key={`${item.source_type}:${item.source_id}`} className="rounded-lg bg-surface-container p-m">
        <p data-type="caption" className="text-on-surface-low">{item.years_ago} {item.years_ago === 1 ? 'year' : 'years'} ago · {item.source_type}</p>
        <h2 data-type="label-l"><a className="text-primary underline" href={item.source_link}>{item.title || 'Untitled'}</a></h2>
        <time data-type="caption" className="text-on-surface-low">{item.original_at}</time><p className="break-words text-on-surface-var">{item.excerpt}</p>
      </li>)}</ul>
      <div className="flex gap-m"><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous</Button><Button disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset ?? offset)}>Next</Button></div>
    </> : null}
  </div></ListScaffold></main>
}
