import { useEffect, useRef, useState } from 'react'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import type { BrowserClient } from './browserClient'
import type { BrowserPreview as PreviewFrame, BrowserSession } from './browserTypes'

type Snapshot = { key: string; frame: PreviewFrame; imageUrl: string }

export default function BrowserPreview({ client, session }: { client: BrowserClient; session: BrowserSession }) {
  const { palette } = useShellTheme()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const imageUrl = useRef<string | null>(null)
  const key = `${client.scope.cacheKey}:${session.id}:${session.version}:${session.controlHolder}`
  const activePreview = useRef({ key, client })
  activePreview.current = { key, client }
  const fresh = snapshot?.key === key && snapshot.frame.version === session.version &&
    snapshot.frame.controlHolder === session.controlHolder

  useEffect(() => {
    let current = true
    let fetching = false
    setError('')
    setSnapshot(null)
    if (imageUrl.current) URL.revokeObjectURL(imageUrl.current)
    imageUrl.current = null
    if (session.status !== 'active') return () => { current = false }

    async function load() {
      if (fetching) return
      fetching = true
      const result = await client.preview(session)
      fetching = false
      if (!current || activePreview.current.key !== key || activePreview.current.client !== client) return
      if (result.state !== 'ready') { setError(result.message); return }
      const nextUrl = URL.createObjectURL(result.value.image)
      if (imageUrl.current) URL.revokeObjectURL(imageUrl.current)
      imageUrl.current = nextUrl
      setSnapshot({ key, frame: result.value, imageUrl: nextUrl })
      setError('')
    }

    void load()
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden') void load() }, 2000)
    return () => {
      current = false
      window.clearInterval(timer)
      if (imageUrl.current) URL.revokeObjectURL(imageUrl.current)
      imageUrl.current = null
    }
  }, [client, key, refresh, session.status])

  const frame = fresh ? snapshot.frame : null
  const stamp = frame ? new Date(frame.timestamp < 1e12 ? frame.timestamp * 1000 : frame.timestamp) : null
  return <section aria-label="Browser preview" style={{ minWidth: 0, border: `1px solid ${palette.line}`,
    borderRadius: 14, padding: 16, background: palette.card, color: palette.text, display: 'grid', gap: 12 }}>
    <header style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
      <div style={{ flex: 1, minWidth: 0 }}><h2 style={{ margin: 0 }}>Live preview</h2>
        <p style={{ margin: '4px 0 0', color: palette.muted, overflowWrap: 'anywhere' }}>
          {frame?.title || (session.status === 'active' ? 'Waiting for the current page' : 'Connect the browser to view its page')}
        </p></div>
      <button type="button" onClick={() => setRefresh(value => value + 1)}
        disabled={session.status !== 'active'} style={{ minHeight: 44, padding: '8px 14px', borderRadius: 9,
          background: palette.secondary, color: palette.text, border: `1px solid ${palette.line}` }}>Refresh preview</button>
    </header>
    {frame && <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', color: palette.muted, fontSize: 13 }}>
      <span style={{ overflowWrap: 'anywhere' }}>{frame.url || 'Blank page'}</span>
      <span>{frame.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'}</span>
      <span>{stamp?.toLocaleTimeString()}</span>
    </div>}
    {error && <p role="alert" style={{ color: palette.danger, margin: 0 }}>{error}</p>}
    {!fresh && session.status === 'active' && <p role="status" style={{ margin: 0 }}>Waiting for a current preview. Refresh before using this image.</p>}
    {frame && snapshot && <div style={{ borderRadius: 10, border: `1px solid ${palette.line}`, overflow: 'hidden',
      background: palette.canvas, minHeight: 180 }}>
      <img src={snapshot.imageUrl} alt={`Browser page: ${frame.title || frame.url || 'blank page'}`}
        data-version={frame.version} data-control-holder={frame.controlHolder}
        draggable={false} style={{ width: '100%', height: 'auto', display: 'block', pointerEvents: 'none', userSelect: 'none' }} />
    </div>}
  </section>
}
