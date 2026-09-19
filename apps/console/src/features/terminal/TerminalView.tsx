import { useEffect, useId, useRef, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import { Folder, RotateCw, Plug } from 'lucide-react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useMode } from '../../app/shell/theme'
import { api } from '../../shared/data/api'
import { registerTerminal } from './terminalBridge'
import { escapeGate } from './escapeGate'
import type { TermTab } from './TerminalPage'

type Status = 'connecting' | 'open' | 'reconnecting' | 'exited' | 'error'

const HINT_MS = 4000

export function TerminalView({ tab, onExited, onClose, onSession }: { tab: TermTab; onExited: () => void; onClose: () => void
  onSession?: (sessionId: string) => void }) {
  const { mode } = useMode()
  const hostRef = useRef<HTMLDivElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)
  const hintId = useId()
  const [hintRead, setHintRead] = useState(false)
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [exitCode, setExitCode] = useState<number | null>(null)
  const [restartKey, setRestartKey] = useState(0)
  const [cwd, setCwd] = useState(tab.cwd ?? '')
  const sessionIdRef = useRef(tab.id)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const term = new Terminal({
      fontSize: 13,
      fontFamily: '"Google Sans Code", "MesloLGS NF", "FiraCode Nerd Font", "Hack Nerd Font", "JetBrainsMono Nerd Font", "Symbols Nerd Font", "Powerline Symbols", ui-monospace, monospace',
      cursorBlink: true,
      theme: mode === 'light'
        ? { background: '#ffffff', foreground: '#1a1a1a' }
        : { background: '#0d0d12', foreground: '#e6e6ee' },
    })
    const fit = new FitAddon(); term.loadAddon(fit); term.loadAddon(new WebLinksAddon())
    term.open(host); try { fit.fit() } catch {   }
    termRef.current = term
    const cwdHandlers = [
      term.parser.registerOscHandler(7, (data) => {
        try {
          const next = new URL(data).pathname
          if (next) setCwd(decodeURIComponent(next))
        } catch {   }
        return true
      }),
      term.parser.registerOscHandler(1337, (data) => {
        if (!data.startsWith('CurrentDir=')) return false
        const next = data.slice('CurrentDir='.length)
        if (next) setCwd(next)
        return true
      }),
    ]

    let lastEsc = 0
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== 'keydown') return true
      const d = escapeGate(e.key, e.timeStamp, lastEsc)
      lastEsc = d.lastEscAt
      if (d.release) shellRef.current?.focus()
      return d.forward
    })
    const helper = host.querySelector('.xterm-helper-textarea')
    helper?.setAttribute('tabindex', '-1')
    helper?.setAttribute('aria-describedby', hintId)

    let disposed = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0
    let unregisterBridge = () => {}

    const connect = () => {
      if (disposed) return
      const sid = sessionIdRef.current
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/api/ws/terminal/${encodeURIComponent(sid)}`)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      ws.onopen = () => {
        attempts = 0
        setStatus('open'); setExitCode(null)
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          try {
            const m = JSON.parse(e.data)
            if (m.type === 'exited') { unregisterBridge(); setExitCode(typeof m.code === 'number' ? m.code : null); setStatus('exited'); onExited() }
            else if (m.type === 'error') setStatus('error')
            else if (m.type === 'cwd' && typeof m.cwd === 'string' && m.cwd) setCwd(m.cwd)
          } catch {   }
          return
        }
        term.write(new Uint8Array(e.data as ArrayBuffer))
      }
      ws.onclose = () => {
        if (disposed) return
        setStatus((s) => {
          if (s === 'exited' || s === 'error') return s
          attempts += 1
          const delay = Math.min(1000 * 2 ** (attempts - 1), 8000)
          reconnectTimer = setTimeout(connect, delay)
          return 'reconnecting'
        })
      }

      const onData = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(new TextEncoder().encode(d)) })
      ;(ws as any)._onData = onData
    }
    connect()

    const ro = new ResizeObserver(() => {
      try { fit.fit() } catch {   }
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    })
    ro.observe(host)

    const boundSession = sessionIdRef.current
    unregisterBridge = registerTerminal(boundSession, (text: string) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) { ws.send(new TextEncoder().encode(text)); return true }
      return false
    })

    return () => {
      disposed = true
      clearTimeout(reconnectTimer)
      unregisterBridge()
      const ws = wsRef.current
      try { (ws as any)?._onData?.dispose?.() } catch {   }
      cwdHandlers.forEach((handler) => handler.dispose())
      ro.disconnect(); ws?.close(); term.dispose()
    }
  }, [restartKey]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const t = termRef.current
    if (t) t.options.theme = mode === 'light' ? { background: '#ffffff', foreground: '#1a1a1a' } : { background: '#0d0d12', foreground: '#e6e6ee' }
  }, [mode])

  useEffect(() => {
    const el = shellRef.current
    if (!el) return
    let t: ReturnType<typeof setTimeout> | undefined
    const onIn = () => { setHintRead(false); clearTimeout(t); t = setTimeout(() => setHintRead(true), HINT_MS) }
    const onOut = () => { clearTimeout(t); setHintRead(false) }
    el.addEventListener('focusin', onIn)
    el.addEventListener('focusout', onOut)
    return () => { el.removeEventListener('focusin', onIn); el.removeEventListener('focusout', onOut); clearTimeout(t) }
  }, [])

  async function restart() {
    setStatus('connecting'); setExitCode(null)
    try {
      const r = await api.createTerminal(tab.cwd, tab.sandbox)
      sessionIdRef.current = r.session_id
      setCwd(r.cwd)
      onSession?.(r.session_id)
    } catch {   }
    termRef.current?.clear()
    setRestartKey((k) => k + 1)
  }

  return (
    <div ref={shellRef} tabIndex={0} role="group" aria-label="Terminal session"
      aria-describedby={hintId}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); termRef.current?.focus() }
      }}
      className="group relative flex h-full flex-col overflow-hidden rounded-lg border border-outline/30 outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
      style={{ background: mode === 'light' ? '#ffffff' : '#0d0d12' }}>
      <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-outline/20 px-2 text-[0.6875rem] text-on-surface-low"
        aria-label={`Current directory: ${cwd || 'unavailable'}`} title={cwd || 'Current directory unavailable'}>
        <Folder size={11} className="shrink-0" />
        <span className="truncate font-mono">{cwd || 'Current directory unavailable'}</span>
      </div>
      <div ref={hostRef} className="min-h-0 w-full flex-1" />

      {
}
      <div id={hintId}
        className={`pointer-events-none absolute right-2 top-8 rounded-pill bg-surface-high/90 px-2 py-0.5 text-on-surface-low text-[0.6875rem] transition-opacity ${hintRead ? 'opacity-0' : 'opacity-0 group-focus-within:opacity-100'}`}>
        Enter to type here · Esc Esc to leave
      </div>

      {status === 'reconnecting' && (
        <div className="absolute inset-x-0 top-0 flex items-center justify-center gap-1.5 bg-warning/20 px-3 py-1 text-center text-[0.75rem] text-on-surface">
          <Plug size={11} className="animate-pulse" /> Reconnecting…
        </div>
      )}
      {(status === 'exited' || status === 'error') && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-canvas/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="text-on-surface text-[0.9375rem]" style={fvs(500)}>
              {status === 'error' ? 'Session error' : exitCode ? `Process exited (code ${exitCode})` : 'Process exited'}
            </div>
            <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">The shell session has ended.</div>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={restart}
              className="inline-flex items-center gap-1.5 rounded-pill px-4 h-9 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
              <RotateCw size={14} /> Restart
            </button>
            <button type="button" onClick={onClose}
              className="rounded-pill px-4 h-9 text-on-surface-low text-[0.8125rem] hover:bg-surface-high hover:text-on-surface">Close tab</button>
          </div>
        </div>
      )}
    </div>
  )
}
