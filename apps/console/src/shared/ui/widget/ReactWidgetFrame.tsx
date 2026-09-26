import { useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertTriangle, ExternalLink, Maximize2, Minimize2 } from 'lucide-react'
import { useMode } from '../../../app/shell/theme'
import { SquareIconButton } from '../SquareIconButton'
import { buildReactSrcdoc, readThemeVars } from './widgetSrcdoc'
import { useWidgetWire } from './useWidgetActionBridge'
import { exportWidget, useWidgetDocument, useWidgetExpansion } from './widgetFrameState'

interface ReactWidgetFrameProps { jsx: string; title?: string; onReady?: () => void; onError?: (message: string) => void }
export function ReactWidgetFrame({ jsx, title = 'React widget', onReady, onError }: ReactWidgetFrameProps) {
  const { mode } = useMode()
  const frame = useRef<HTMLIFrameElement>(null)
  const [height, setHeight] = useState(240)
  const [error, setError] = useState<string | null>(null)
  const expansion = useWidgetExpansion()
  const vars = useMemo(() => readThemeVars(), [mode])
  const source = useMemo(() => buildReactSrcdoc({ jsx, themeVars: vars, mode }), [jsx, vars, mode])
  const url = useWidgetDocument(source)
  useEffect(() => setError(null), [source])
  useWidgetWire(frame, { onHeight: value => setHeight(Math.max(80, Math.min(640, value))), onReady, onError: message => { setError(message); onError?.(message) } })
  return <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
    className={expansion.expanded ? 'fixed inset-4 z-[var(--z-content)] flex flex-col rounded-2xl border border-outline-variant bg-surface shadow-2xl' : 'relative my-3 overflow-hidden rounded-xl border border-outline-variant bg-surface'}>
    <div className="flex min-h-11 items-center justify-between gap-3 border-b border-outline-variant bg-surface-high/40 px-3 py-1.5">
      <div className="flex min-w-0 items-center gap-2"><span className="truncate text-xs font-medium text-on-surface">{title}</span>
        {error && <span className="inline-flex items-center gap-1 text-xs text-danger" title={error}><AlertTriangle size={12} />error</span>}
      </div>
      <div className="flex gap-1" role="group" aria-label={`${title} actions`}>
        <SquareIconButton label="Open in new tab" icon={ExternalLink} onClick={() => exportWidget(source, title, 'tab')} />
        <SquareIconButton label={expansion.expanded ? 'Minimize' : 'Expand'} icon={expansion.expanded ? Minimize2 : Maximize2} onClick={expansion.toggle} />
      </div>
    </div>
    {url && <iframe ref={frame} src={url} sandbox="allow-scripts" title={title} className="w-full border-none bg-surface" style={{ height: expansion.expanded ? 'calc(100% - 44px)' : height }} />}
    {expansion.expanded && <div className="fixed inset-0 -z-10 bg-black/55 backdrop-blur-sm" onClick={expansion.close} />}
  </motion.div>
}
