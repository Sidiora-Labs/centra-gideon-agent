import { useCallback, useEffect, useState } from 'react'
import { Terminal as TermIcon, Plus, X, Loader2, SplitSquareHorizontal, Anchor } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { api } from '../../shared/data/api'
import { panesAfterClose, type PaneSelection } from './paneState'
import { TerminalView } from './TerminalView'
import { PageTitle } from '../../shared/ui/PageTitle'
import { tabListKeys } from '../../shared/data/tabListKeys'
import { SandboxPicker, type SandboxProvider } from './SandboxPicker'
import { persistClaim } from '../../lib/persistClaim'

export interface TermTab { id: string; label: string; cwd?: string; shell?: string; sandbox?: string; custom?: boolean }

const LABELS_KEY = 'terminal-labels'

function loadLabels(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(LABELS_KEY) || '{}') } catch { return {} }
}
function saveLabel(id: string, label: string) {
  const m = loadLabels(); m[id] = label; localStorage.setItem(LABELS_KEY, JSON.stringify(m))
}

export function TerminalPage({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [tabs, setTabs] = useState<TermTab[]>([])
  const [activeRaw, setActiveQ] = useQueryParam(query, setQuery, 'active', '')
  const active = activeRaw
  const setActive = (id: string) => setActiveQ(id)
  const [splitRaw, setSplitQ] = useQueryParam(query, setQuery, 'split', '')
  const split = splitRaw || null
  const setSplit = (id: string | null) => setSplitQ(id ?? '')
  const setPanes = (p: PaneSelection) => setQuery({ active: p.active || null, split: p.split || null })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [restored, setRestored] = useState(false)
  const [persist, setPersist] = useState<boolean | null>(null)
  const [persistAvailable, setPersistAvailable] = useState<boolean | undefined>()
  const persistenceConfirmed = persistClaim(persist, persistAvailable)
  useEffect(() => {
    api.gideonConfig()
      .then((c) => setPersist(Boolean(c?.dashboard?.terminal?.persist)))
      .catch(() => setPersist(false))
  }, [])
  const [providers, setProviders] = useState<SandboxProvider[]>([])
  const [sandbox, setSandbox] = useState('none')
  useEffect(() => {
    api.sandboxProviders()
      .then((r) => setProviders(r.providers || []))
      .catch(() => setProviders([]))
  }, [])
  const togglePersist = () => {
    const next = !persist
    setPersist(next)
    setError('')
    api.patchConfig('dashboard.terminal.persist', next).catch((e) => {
      setPersist(!next)
      setError(`Couldn't ${next ? 'enable' : 'disable'} persistent sessions: ${e instanceof Error ? e.message : String(e)}`)
    })
  }

  useEffect(() => {
    let alive = true
    api.terminalSessions().then((r) => {
      if (!alive) return
      setPersistAvailable(r.persist_available)
      const labels = loadLabels()
      const live = (r.sessions || []).filter((s) => s.alive !== false)
      if (live.length) {
        const restoredTabs = live.map((s, i) => ({
          id: s.session_id, cwd: s.cwd, shell: s.shell,
          label: labels[s.session_id] || `Session ${i + 1}`,
          custom: !!labels[s.session_id],
        }))
        setTabs(restoredTabs)
        const wanted = restoredTabs.find((t) => t.id === active)
        if (!wanted) setActive(restoredTabs[0].id)
      }
      setRestored(true)
    }).catch(() => setRestored(true))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const newSession = useCallback(async (intoSplit = false) => {
    setBusy(true); setError('')
    try {
      const r = await api.createTerminal(undefined, sandbox === 'none' ? undefined : sandbox)
      setTabs((t) => {
        const tab: TermTab = { id: r.session_id, label: `Session ${t.length + 1}`, cwd: r.cwd, shell: r.shell, sandbox: r.sandbox || undefined }
        return [...t, tab]
      })
      if (intoSplit) setSplit(r.session_id)
      else setActive(r.session_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open a terminal session.')
    } finally { setBusy(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sandbox])

  const closeSession = useCallback(async (id: string) => {
    try {
      await api.deleteTerminal(id)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not close the terminal session.')
      return
    }
    setError('')
    const next = tabs.filter((x) => x.id !== id)
    setTabs(next)
    setPanes(panesAfterClose(next.map((x) => x.id), id, { active, split }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabs, active, split])

  const rename = useCallback((id: string, label: string) => {
    saveLabel(id, label)
    setTabs((t) => t.map((x) => (x.id === id ? { ...x, label, custom: true } : x)))
  }, [])

  const onExited = useCallback(() => {   }, [])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<PageTitle>Terminal</PageTitle>}
        right={<HeaderActions>
          {persistAvailable === true && persist !== null && (
            <HeaderControl icon={Anchor}
              label={persist ? 'Disable persistent sessions' : 'Enable persistent sessions'}
              hint={persistenceConfirmed
                ? 'Confirmed active: sessions are tmux-backed, so they survive a restart.'
                : 'Sessions are lost on restart. Enabling keeps them alive with tmux.'}
              active={persistenceConfirmed} priority="low" onClick={togglePersist} />
          )}
          {persistAvailable === false && (
            <HeaderControl icon={Anchor} label="Persistent sessions unavailable"
              hint="Install tmux to keep terminal sessions alive across restarts."
              disabled priority="low" />
          )}
          {tabs.length > 0 && (
            <HeaderControl icon={SplitSquareHorizontal} label={split ? 'Close split' : 'Split right'}
              active={!!split}
              onClick={() => { if (split) setSplit(null); else if (tabs.length >= 2) setSplit(tabs.find((t) => t.id !== active)?.id ?? null); else newSession(true) }} />
          )}
          {providers.some((p) => p.name !== 'none') && (
            <SandboxPicker providers={providers} value={sandbox} onChange={setSandbox} busy={busy} />
          )}
          <HeaderControl icon={busy ? Loader2 : Plus} label="New terminal session" priority="primary" onClick={() => newSession(false)} />
        </HeaderActions>}
      />

      {error && <InlineError icon className="mx-2 mt-2" onDismiss={() => setError('')}>{error}</InlineError>}

      {tabs.length > 0 && (
        <div role="tablist" aria-label="Terminal sessions" onKeyDown={tabListKeys((i) => setActive(tabs[i].id))}
          className="flex items-stretch gap-1 overflow-x-auto border-b border-outline/40 px-2 pt-2">
          {tabs.map((t) => (
            <TermTabChip key={t.id} tab={t} active={t.id === active} inSplit={t.id === split}
              onSelect={() => setActive(t.id)} onClose={() => closeSession(t.id)} onRename={(l) => rename(t.id, l)} />
          ))}
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {!restored ? (
          <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
        ) : tabs.length === 0 ? (
          <EmptyState icon={TermIcon} title="No terminal sessions" hint="Open a PTY session to run shell commands in your workspace." action={{ label: 'New session', onClick: () => newSession(false), icon: Plus }} />
        ) : (
          <div className="absolute inset-0 flex gap-px p-2">
            {tabs.map((t) => {
              const visible = t.id === active || t.id === split
              const pane = t.id === active ? 'left' : t.id === split ? 'right' : null
              return (
                <div key={t.id} className="min-w-0 flex-1"
                  style={{ display: visible ? 'block' : 'none', order: pane === 'right' ? 2 : 1 }}>
                  <TerminalView tab={t} onExited={onExited} onClose={() => closeSession(t.id)} />
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function TermTabChip({ tab, active, inSplit, onSelect, onClose, onRename }: {
  tab: TermTab; active: boolean; inSplit: boolean
  onSelect: () => void; onClose: () => void; onRename: (label: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [v, setV] = useState(tab.label)
  const on = active || inSplit
  const chrome = on
    ? { background: 'var(--color-surface-container)', color: 'var(--color-on-surface)', borderColor: 'var(--color-outline)' }
    : { color: 'var(--color-on-surface-low)', borderColor: 'transparent' }
  return (
    <div role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
      onClick={onSelect} title={tab.cwd || tab.id}
      aria-keyshortcuts="F2 Delete"
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); return }
        if (e.key === 'F2') { e.preventDefault(); setV(tab.label); setEditing(true) }
        if (e.key === 'Delete') { e.preventDefault(); onClose() }
      }}
      style={chrome}
      className="group inline-flex h-9 shrink-0 cursor-pointer items-center gap-1 rounded-t-lg border border-b-0 pl-3 pr-1.5 text-[0.8125rem] transition-colors"
    >
      <TermIcon size={13} className={inSplit && !active ? 'text-primary' : 'opacity-70'} />
      {editing ? (
        <input autoFocus aria-label={`Rename ${tab.label}`} value={v} onChange={(e) => setV(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={() => { setEditing(false); if (v.trim()) onRename(v.trim()) }}
          onKeyDown={(e) => { e.stopPropagation(); if (e.key === 'Enter') { setEditing(false); if (v.trim()) onRename(v.trim()) } if (e.key === 'Escape') { setEditing(false); setV(tab.label) } }}
          className="w-24 rounded bg-transparent text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      ) : (
        <span className="mr-1" onDoubleClick={(e) => { e.stopPropagation(); setV(tab.label); setEditing(true) }}>{tab.label}</span>
      )}
      {
}
      <button type="button" onClick={(e) => { e.stopPropagation(); onClose() }}
        aria-label={`Close ${tab.label}`} title="Close session"
        className="grid size-6 -mr-0.5 shrink-0 place-items-center rounded opacity-50 hover:bg-surface-high hover:opacity-100"><X size={12} /></button>
    </div>
  )
}
