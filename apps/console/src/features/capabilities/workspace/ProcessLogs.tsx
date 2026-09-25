import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Window = { text: string; start: number; next: number; end: number; dropped: number; status: string }
export default function ProcessLogs({ id }: { id: string }) {
  const [following, setFollowing] = useState(false)
  const [text, setText] = useState('')
  const [query, setQuery] = useState('')
  const [dropped, setDropped] = useState(0)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('Not read')
  const cursor = useRef(0)
  useEffect(() => {
    if (!following) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const value = await requestJson<Window>(`/api/capabilities/workspace/processes/${encodeURIComponent(id)}/log-window?after=${cursor.current}&limit=65536`)
        if (!active) return
        cursor.current = value.next
        setDropped(old => old + value.dropped)
        setText(old => (old + value.text).slice(-65536))
        setStatus(value.status); setError('')
        if (value.next < value.end || ['starting', 'running'].includes(value.status)) timer = setTimeout(poll, 1000)
        else setFollowing(false)
      } catch (e) { if (active) { setError(String(e)); setFollowing(false) } }
    }
    void poll()
    return () => { active = false; clearTimeout(timer) }
  }, [following, id])
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = `process-${id}.log`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const visible = query ? text.split('\n').filter(line => line.toLowerCase().includes(query.toLowerCase())).join('\n') : text
  return <section aria-label="Live process log" className="space-y-2">
    <div className="flex flex-wrap gap-2"><Button onClick={() => setFollowing(value => !value)}>{following ? 'Pause live log' : 'Follow process log'}</Button><Button disabled={!text} onClick={download}>Download retained log</Button></div>
    <label className="block">Filter retained lines <input aria-label="Filter retained log lines" maxLength={256} value={query} onChange={e => setQuery(e.target.value)} className="border rounded bg-surface p-2 max-w-full" /></label>
    <p>Status: {status} · Cursor: {cursor.current} · {dropped} characters missed before retained window. Displays the latest 65,536 characters.</p>
    {error && <p role="alert">{error}</p>}
    <pre aria-label="Live log output" className="max-h-72 overflow-auto whitespace-pre-wrap break-words">{visible || 'No matching output yet.'}</pre>
  </section>
}
