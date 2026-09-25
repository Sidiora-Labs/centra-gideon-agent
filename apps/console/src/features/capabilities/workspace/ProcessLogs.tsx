import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

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
  return <Surface className="p-l"><section aria-label="Live process log" className="space-y-m">
    <div className="flex flex-wrap items-center justify-between gap-m"><h3 data-type="title-m">Live process log</h3><div className="flex flex-wrap gap-s"><Button size="sm" onClick={() => setFollowing(value => !value)}>{following ? 'Pause live log' : 'Follow process log'}</Button><Button size="sm" variant="secondary" disabled={!text} onClick={download}>Download retained log</Button></div></div>
    <Field label="Filter retained lines"><TextInput ariaLabel="Filter retained log lines" maxLength={256} value={query} onChange={setQuery}/></Field>
    <p data-type="body-s" className="text-on-surface-low">Status: {status} · Cursor: {cursor.current} · {dropped} characters missed before retained window. Displays the latest 65,536 characters.</p>
    {error && <p role="alert">{error}</p>}
    <pre aria-label="Live log output" className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface p-m">{visible || 'No matching output yet.'}</pre>
  </section></Surface>
}
