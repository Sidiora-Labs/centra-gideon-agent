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
  const identity = JSON.stringify([client.scope.runtimeOrigin, client.scope.cacheKey, client.scope.ownerId, session.id])
  const scopeRef = useRef({ identity, generation: 0 })
  if (scopeRef.current.identity !== identity) {
    scopeRef.current = { identity, generation: scopeRef.current.generation + 1 }
    currentRef.current = session
  } else if (session.version > currentRef.current.version) {
    currentRef.current = session
  }
  const [stateIdentity, setStateIdentity] = useState(identity)
  const scopeChanged = stateIdentity !== identity
  const [fresh, setFresh] = useState(true)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState<BrowserPreview | null>(null)
  const [url, setUrl] = useState('')
  const [inputText, setInputText] = useState('')

  function live(generation: number, base: BrowserSession) {
    return scopeRef.current.generation === generation && scopeRef.current.identity === identity &&
      currentRef.current.id === session.id &&
      currentRef.current.version === base.version
  }

  function accept(next: BrowserSession, generation: number, base: BrowserSession) {
    if (!live(generation, base) || next.id !== base.id || next.version < base.version) return false
    currentRef.current = next
    setCurrent(next)
    onSessionChange?.(next)
    return true
  }

  useEffect(() => {
    if (stateIdentity !== identity) {
      setStateIdentity(identity)
      currentRef.current = session
      setCurrent(session)
      setFresh(true)
      setBusy(false)
      setPreview(null)
      setMessage('')
      setUrl('')
      setInputText('')
    } else if (session.id === currentRef.current.id && session.version >= currentRef.current.version) {
      currentRef.current = session
      setCurrent(session)
      setFresh(true)
      setBusy(false)
      setPreview(null)
    }
  }, [identity, session, stateIdentity])

  async function refresh() {
    const generation = scopeRef.current.generation
    const base = currentRef.current
    setBusy(true)
    const result = await client.get(base.id)
    if (!live(generation, base)) return
    if (result.state === 'ready' && accept(result.value, generation, base)) {
      setFresh(true)
      setMessage('Browser state refreshed.')
    } else if (result.state !== 'ready') {
      setFresh(false)
      setMessage(result.message)
    }
    setPreview(null)
    setBusy(false)
  }

  async function showPreview() {
    if (busy || !fresh || currentRef.current.status !== 'active') return
    const generation = scopeRef.current.generation
    const base = currentRef.current
    setBusy(true)
    const result = await client.preview(base)
    if (!live(generation, base)) return
    if (result.state === 'ready' && result.value.version === base.version &&
        result.value.controlHolder === base.controlHolder) {
      setPreview(result.value)
      setMessage('')
    } else {
      setPreview(null)
      setFresh(false)
      setMessage(result.state === 'ready' ? 'The browser changed while the preview loaded.' : result.message)
      const latest = await client.get(base.id)
      if (!live(generation, base)) return
      if (latest.state === 'ready' && accept(latest.value, generation, base)) setFresh(true)
    }
    setBusy(false)
  }

  async function act(operation: (current: BrowserSession) => Promise<BrowserResult<BrowserSession>>) {
    if (busy || !fresh) return
    const generation = scopeRef.current.generation
    const base = currentRef.current
    setBusy(true)
    const result = await operation(base)
    if (!live(generation, base)) return
    setPreview(null)
    if (result.state === 'ready') {
      if (!accept(result.value, generation, base)) { setBusy(false); return }
      setMessage('')
    } else {
      setMessage(result.message)
      setFresh(false)
      let latestBase = base
      if (result.current && accept(result.current, generation, base)) latestBase = result.current
      const latest = await client.get(latestBase.id)
      if (!live(generation, latestBase)) return
      if (latest.state === 'ready' && accept(latest.value, generation, latestBase)) setFresh(true)
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

  const visibleCurrent = scopeChanged ? session : session.version > current.version ? session : current
  const visibleFresh = scopeChanged ? true : fresh
  const visibleBusy = scopeChanged ? false : busy
  const visibleMessage = scopeChanged ? '' : message
  const visiblePreview = scopeChanged ? null : preview
  const visibleUrl = scopeChanged ? '' : url
  const visibleInputText = scopeChanged ? '' : inputText
  const customer = visibleCurrent.status === 'active' && visibleCurrent.controlHolder === 'customer'
  const canAct = visibleFresh && !visibleBusy
  const button = { minHeight: 44, border: `1px solid ${palette.line}`, borderRadius: 8,
    padding: '8px 12px', background: palette.secondary, color: palette.text, cursor: 'pointer' }
  const field = { minHeight: 44, border: `1px solid ${palette.line}`, borderRadius: 8,
    padding: '8px 12px', background: palette.card, color: palette.text, flex: '1 1 180px' }

  return <section aria-label="Browser controls" style={{ display: 'grid', gap: 14, padding: 18,
    border: `1px solid ${palette.line}`, borderRadius: 14, background: palette.card, color: palette.text }}>
    <div>
      <h2 style={{ margin: '0 0 6px' }}>Browser controls</h2>
      <p role="status" style={{ margin: 0, color: palette.muted }}>
        {visibleCurrent.status === 'active' ? 'Connected' : visibleCurrent.status === 'reserved' ? 'Ready to connect' :
          visibleCurrent.status === 'closed' ? 'Closed' : 'Connection unavailable'} ·
        {' '}{visibleCurrent.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'} · version {visibleCurrent.version}
      </p>
    </div>
    {visibleMessage && <p role="alert" style={{ margin: 0, color: palette.danger }}>{visibleMessage}</p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {visibleCurrent.status === 'reserved' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('start')}>Connect browser</button>}
      {(visibleCurrent.status === 'closed' || visibleCurrent.status === 'error') && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('reopen')}>Reopen browser</button>}
      {visibleCurrent.status !== 'closed' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('close')}>Close browser</button>}
      {visibleCurrent.status === 'active' && visibleCurrent.controlHolder === 'assistant' && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('takeover')}>Take control</button>}
      {customer && <button type="button" style={button} disabled={!canAct} onClick={() => mutate('handback')}>Hand back to Gideon</button>}
      <button type="button" style={button} disabled={visibleBusy} onClick={() => void refresh()}>Refresh state</button>
      <button type="button" style={button} disabled={!canAct || visibleCurrent.status !== 'active'} onClick={() => void showPreview()}>Refresh preview</button>
    </div>
    {visibleCurrent.status === 'active' && <div aria-label="Browser preview" style={{ display: 'grid', gap: 8 }}>
      {visiblePreview && visiblePreview.version === visibleCurrent.version ? <>
        <p style={{ margin: 0, overflowWrap: 'anywhere' }}>{visiblePreview.title || 'Untitled page'} · {visiblePreview.url || 'Blank page'}</p>
        <p style={{ margin: 0, color: palette.muted }}>Captured {new Date(visiblePreview.timestamp * 1000).toLocaleString()} · {visiblePreview.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'} · version {visiblePreview.version} · {visiblePreview.image.size} image bytes</p>
      </> : <p style={{ margin: 0, color: palette.muted }}>Preview unavailable or stale. Refresh the preview to see the current page.</p>}
    </div>}
    {customer && <>
      <form onSubmit={navigate} style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <label htmlFor={`browser-url-${visibleCurrent.id}`} style={{ flexBasis: '100%' }}>Address</label>
        <input id={`browser-url-${visibleCurrent.id}`} type="url" required value={visibleUrl} onChange={event => setUrl(event.target.value)} placeholder="https://example.com" style={field} disabled={!canAct} />
        <button type="submit" style={button} disabled={!canAct}>Navigate</button>
      </form>
      <form onSubmit={insertText} style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <label htmlFor={`browser-text-${visibleCurrent.id}`} style={{ flexBasis: '100%' }}>Type into the focused page field</label>
        <input id={`browser-text-${visibleCurrent.id}`} value={visibleInputText} onChange={event => setInputText(event.target.value)} maxLength={2000} style={field} disabled={!canAct} />
        <button type="submit" style={button} disabled={!canAct || !visibleInputText}>Type text</button>
      </form>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'scroll', 'up'))}>Scroll up</button>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'scroll', 'down'))}>Scroll down</button>
        <button type="button" style={button} disabled={!canAct} onClick={() => void act(row => client.input(row, 'key', 'Enter'))}>Press Enter</button>
      </div>
    </>}
  </section>
}
