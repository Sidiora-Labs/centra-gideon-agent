import { useState } from 'react'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import type { BrowserClient } from './browserClient'
import type { BrowserSession } from './browserTypes'
import BrowserControls from './BrowserControls.web'
import BrowserPreview from './BrowserPreview.web'

type Props = {
  client: BrowserClient
  session: BrowserSession
  onSessionChange: (session: BrowserSession) => void
}

export default function BrowserWorkspace({ client, session, onSessionChange }: Props) {
  const { palette } = useShellTheme()
  const [expanded, setExpanded] = useState(true)

  return <div aria-label="Browser workspace" style={{ display: 'grid', gap: 16,
    width: '100%', minWidth: 0, color: palette.text }}>
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
      <div style={{ flex: '1 1 240px', minWidth: 0 }}>
        <h2 style={{ margin: 0 }}>Your conversation browser</h2>
        <p style={{ margin: '4px 0 0', color: palette.muted }}>
          {session.status === 'active' ? 'Connected' : session.status === 'reserved' ? 'Ready to connect' :
            session.status === 'closed' ? 'Closed' : 'Connection unavailable'} ·{' '}
          {session.controlHolder === 'customer' ? 'You have control' : 'Gideon has control'}
        </p>
      </div>
      <button type="button" aria-expanded={expanded} aria-controls={`browser-controls-${session.id}`}
        onClick={() => setExpanded(value => !value)} style={{ minHeight: 44, padding: '8px 14px',
          border: `1px solid ${palette.line}`, borderRadius: 9, background: palette.secondary, color: palette.text }}>
        {expanded ? 'Hide controls' : 'Show controls'}
      </button>
    </div>
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))',
      gap: 16, alignItems: 'start', minWidth: 0 }}>
      <BrowserPreview client={client} session={session} />
      <div id={`browser-controls-${session.id}`} hidden={!expanded} style={{ minWidth: 0 }}>
        <BrowserControls client={client} session={session} onSessionChange={onSessionChange} />
      </div>
    </div>
  </div>
}
