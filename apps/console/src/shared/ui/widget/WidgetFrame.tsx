import { useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Bookmark, Download, ExternalLink, Maximize2, Minimize2, Pin, SlidersHorizontal } from 'lucide-react'
import { useMode } from '../../../app/shell/theme'
import { SquareIconButton } from '../SquareIconButton'
import { spring } from '../../theme/motion'
import { buildSrcdoc, readThemeVars } from './widgetSrcdoc'
import { effectiveWidgetSlug } from './widgetSlug'
import { useWidgetWire } from './useWidgetActionBridge'
import { useArtifactIteration } from './useArtifactIteration'
import { ArtifactIterationRail } from './ArtifactIterationRail'
import { BlueprintSkeleton } from './BlueprintSkeleton'
import { exportWidget, useWidgetArtifact, useWidgetDocument, useWidgetExpansion, useWidgetMeasure } from './widgetFrameState'

interface WidgetFrameProps { html: string; title?: string; slug?: string; messageTs?: string; widgetIndex?: number; streaming?: boolean }

export function WidgetFrame({ html, title = 'Widget', slug, messageTs, widgetIndex = 0, streaming = false }: WidgetFrameProps) {
  const { mode } = useMode()
  const frame = useRef<HTMLIFrameElement>(null)
  const wrapper = useRef<HTMLDivElement>(null)
  const expansion = useWidgetExpansion()
  const measure = useWidgetMeasure(html, wrapper)
  const identity = effectiveWidgetSlug({ explicitSlug: slug, messageTs, widgetIndex })
  const artifact = useWidgetArtifact(identity, title, html, streaming)
  const themeVars = useMemo(() => readThemeVars(), [mode])
  const source = useMemo(() => buildSrcdoc({ html, themeVars, mode, includeHost: !streaming, transparentBody: true, editMode: !streaming }), [html, themeVars, mode, streaming])
  const url = useWidgetDocument(source)
  const [railOpen, setRailOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const iteration = useArtifactIteration(frame, { source: html, target: { slug: identity, persistVersion: artifact.persistVersion } })
  useWidgetWire(frame, { forwardActions: true, onHeight: measure.receive, onError: setError, liveArtifact: artifact.liveArtifact, ...iteration.wire })
  const exportSource = () => buildSrcdoc({ html, themeVars, mode, includeHost: false })
  const actions = <div className="flex items-center gap-1" role="group" aria-label={`${title} actions`}>
    <SquareIconButton label={railOpen ? 'Close the iteration rail' : 'Iterate — tweak parameters or mark elements'} icon={SlidersHorizontal} on={railOpen} ariaExpanded={railOpen} onClick={() => setRailOpen(open => !open)} />
    <SquareIconButton label={artifact.saved ? 'Saved — click to remove' : 'Save as artifact'} children={<Bookmark size={14} fill={artifact.saved ? 'currentColor' : 'none'} />} on={artifact.saved} loading={artifact.savePending} onClick={artifact.toggleSave} />
    <SquareIconButton label={artifact.pinned ? 'Pinned to dashboard' : 'Pin to dashboard'} icon={Pin} on={artifact.pinned} disabled={artifact.pinned} loading={artifact.pinPending} onClick={artifact.pin} />
    <SquareIconButton label="Download as HTML" icon={Download} onClick={() => exportWidget(exportSource(), title, 'download')} />
    <SquareIconButton label="Open in new tab" icon={ExternalLink} onClick={() => exportWidget(exportSource(), title, 'tab')} />
    <SquareIconButton label={expansion.expanded ? 'Minimize' : 'Expand'} icon={expansion.expanded ? Minimize2 : Maximize2} onClick={expansion.toggle} />
  </div>
  return <motion.div ref={wrapper} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
    className={expansion.expanded ? 'fixed inset-4 z-[var(--z-content)] flex flex-col rounded-2xl border border-outline-variant bg-surface shadow-2xl' : 'group/widget relative my-3'}
    style={expansion.expanded ? undefined : measure.layout}>
    {expansion.expanded && <div className="flex min-h-11 items-center justify-between gap-3 border-b border-outline-variant px-3 py-1.5">
      <span className="truncate text-xs font-medium text-on-surface">{title}</span>{!streaming && actions}
    </div>}
    <AnimatePresence mode="wait">
      {streaming ? <BlueprintSkeleton key="blueprint" height={240} /> : url && <motion.iframe key="document" ref={frame} src={url} sandbox="allow-scripts" title={title}
        className="w-full border-none bg-transparent" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={spring.effects}
        style={{ height: expansion.expanded ? 'calc(100% - 44px)' : measure.height }} />}
    </AnimatePresence>
    {error && <div role="alert" className="border-t border-outline-variant bg-surface-high px-m py-s text-danger">{error}</div>}
    {railOpen && !streaming && <ArtifactIterationRail it={iteration} onClose={() => setRailOpen(false)} className="border-t border-outline-variant bg-surface" />}
    {!expansion.expanded && !streaming && <div className="absolute right-2 top-2 rounded-lg border border-outline-variant bg-surface/95 p-1 shadow-sm opacity-0 transition-opacity group-hover/widget:opacity-100 focus-within:opacity-100">{actions}</div>}
    {expansion.expanded && <div className="fixed inset-0 -z-10 bg-black/55 backdrop-blur-sm" onClick={expansion.close} />}
  </motion.div>
}
