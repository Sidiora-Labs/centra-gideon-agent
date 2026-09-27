import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import type { BrowserClient } from './browserClient'
import type { BrowserMutation, BrowserPreview, BrowserResult, BrowserSession } from './browserTypes'

type Props = {
  client: BrowserClient
  session: BrowserSession
  onSessionChange?: (session: BrowserSession) => void
}

export default function BrowserControls({ client, session, onSessionChange }: Props) {
  const { palette } = useShellTheme()
  const [current, setCurrent] = useState(session)
  const currentRef = useRef(session)
  const [fresh, setFresh] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<BrowserPreview | null>(null)
  const [url, setUrl] = useState('')
  const [inputText, setInputText] = useState('')

  function accept(next: BrowserSession) {
    currentRef.current = next
    setCurrent(next)
    onSessionChange?.(next)
  }

  useEffect(() => {
    if (session.id !== currentRef.current.id || session.version > currentRef.current.version) {
      currentRef.current = session
      setCurrent(session)
      setFresh(true)
      setPreview(null)
      setMessage('')
    }
  }, [session])

  async function refresh() {
    setBusy(true)
    const result = await client.get(currentRef.current.id)
    if (result.state === 'ready') {
      accept(result.value)
      setFresh(true)
      setMessage('Browser state refreshed.')
    } else {
      setFresh(false)
      setMessage(result.message)
    }
    setPreview(null)
    setBusy(false)
  }

  async function showPreview() {
    if (busy || !fresh || currentRef.current.status !== 'active') return
    setBusy(true)
    const result = await client.preview(currentRef.current)
    if (result.state === 'ready' && result.value.version === currentRef.current.version &&
        result.value.controlHolder === currentRef.current.controlHolder) {
      setPreview(result.value)
      setMessage('')
    } else {
      setPreview(null)
      setFresh(false)
      setMessage(result.state === 'ready' ? 'The browser changed while the preview loaded.' : result.message)
      const latest = await client.get(currentRef.current.id)
      if (latest.state === 'ready') {
        accept(latest.value)
        setFresh(true)
      }
    }
    setBusy(false)
  }

  async function act(operation: (current: BrowserSession) => Promise<BrowserResult<BrowserSession>>) {
    if (busy || !fresh) return
    setBusy(true)
    const result = await operation(currentRef.current)
    setPreview(null)
    if (result.state === 'ready') {
      accept(result.value)
      setMessage('')
    } else {
      setMessage(result.message)
      setFresh(false)
      if (result.current) accept(result.current)
      const latest = await client.get(currentRef.current.id)
      if (latest.state === 'ready') {
        accept(latest.value)
        setFresh(true)
      }
    }
    setBusy(false)
  }

  function mutate(action: BrowserMutation) { void act(row => client.mutate(row, action)) }
  function navigate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (url.trim()) void act(row => client.navigate(row, url.trim()))
  }
  function insertText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inputText) void act(row => client.input(row, 'text', inputText))
  }

  const customer = current.status === 'active' && current.controlHolder === 'customer'
  const canAct = fresh && !busy
  const button = { minHeight: 40, border: `1px solid ${palette.line}`, borderRadius: 8,
    padding: '8px 12px', background: palette.secondary, color: palette.text, cursor: 'pointer' }
  const field = { minHeight: 40, border: `1px solid ${palette.line}`, borderRadius: 8,
    padding: '8px 12px', background: palette.card, color: palette.text, flex: '1 1 180px' }

  return <section aria-label="Browser controls" style={{ display: 'grid', gap: 14, padding: 18,
    border: `1px solid ${palette.line}`, borderRadius: 14, background: palette.card, color: palette.text }}>
    <div>
      <h2 style={{ margin: '0 0 6px' }}>Browser controls</h2>
      <p role="status" style={{ margin: 0, color: palette.muted }}>
        {current.status === 'active' ? 'Connected' : current.status === 'reserved' ? 'Ready to connect' :
          current.status === 'closed' ? 'Closed' : 'Connection unavailable'} ·
        {' '}{current.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'} · version {current.version}
      </p>
    </div>
    {message && <p role="alert" style={{ margin: 0, color: palette.danger }}>{message}</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {current.status === 'reserved' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('start')}>Connect browser</button>}
      {(current.status === 'closed' || current.status === 'error') && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('reopen')}>Reopen browser</button>}
      {current.status !== 'closed' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('close')}>Close browser</button>}
      {current.status === 'active' && current.controlHolder === 'assistant' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('takeover')}>Take control</button>}
      {customer && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('handback')}>Hand back to Gideon</button>}
      <button type="button" style={button} disabled={busy} onClick={() => void refresh()}>Refresh state</button>
      <button type="button" style={button} disabled={!canAct || current.status !== 'active'} onClick={() => void showPreview()}>Refresh preview</button>
    </div>
    {current.status === 'active' && <div aria-label="Browser preview" style={{ display: 'grid', gap: 8 }}>
      {preview && preview.version === current.version ? <>
        <p style={{ margin: 0, overflowWrap: 'anywhere' }}>{preview.title || 'Untitled page'} · {preview.url || 'Blank page'}</p>
        <p style={{ margin: 0, color: palette.muted }}>Captured {new Date(preview.timestamp * 1000).toLocaleString()} · {preview.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'} · version {preview.version} · {preview.image.size} image bytes</p>
      </> : <p style={{ margin: 0, color: palette.muted }}>Preview unavailable or stale. Refresh the preview to see the current page.</p>}
    </div>}
    {customer && <>
      <form onSubmit={navigate} style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <label htmlFor={`browser-url-${current.id}`} style={{ flexBasis: '100%' }}>Address</label>
        <input id={`browser-url-${current.id}`} type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.com" style={field} disabled={!canAct} />
        <button type="submit" style={button} disabled={!canAct}>Navigate</button>
      </form>
      <form onSubmit={insertText} style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <label htmlFor={`browser-text-${current.id}`} style={{ flexBasis: '100%' }}>Type into the focused page field</label>
        <input id={`browser-text-${current.id}`} value={inputText} onChange={event => setInputText(event.target.value)} maxLength={2000} style={field} disabled={!canAct} />
        <button type="submit" style={button} disabled={!canAct || !inputText}>Type text</button>
      </form>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'scroll', 'up'))}>Scroll up</button>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'scroll', 'down'))}>Scroll down</button>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'key', 'Enter'))}>Press Enter</button>
      </div>
    </>}
  </section>
}
