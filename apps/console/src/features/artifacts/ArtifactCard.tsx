import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { api, type Artifact } from '../../shared/data/api'
import { artifactKindMeta, relTime } from '../files/fileMeta'
import { buildSrcdoc, buildReactSrcdoc, readThemeVars } from '../../shared/ui/widget/widgetSrcdoc'
import { resolveContentType, isSandboxed } from '../../shared/ui/content/contentTypes'
import { TileButton } from '../../shared/ui/TileButton'
import { useMode } from '../../app/shell/theme'


export const IFRAME_CAP = 12

const _live = new Map<number, () => void>()
let _nextId = 1

function acquireLiveSlot(demote: () => void): number {
  const id = _nextId++
  _live.set(id, demote)
  if (_live.size > IFRAME_CAP) {
    const oldest = _live.keys().next().value
    if (oldest !== undefined) {
      const fn = _live.get(oldest)
      _live.delete(oldest)
      fn?.()
    }
  }
  return id
}

function releaseLiveSlot(id: number) {
  _live.delete(id)
}

const PREVIEW_SCALE = 0.4

function previewsAsIframe(ctype: ReturnType<typeof resolveContentType>): boolean {
  return isSandboxed(ctype) || ctype.id === 'document' || ctype.id === 'svg'
}

function ExcerptPreview({ content }: { content: string }) {
  return (
    <pre aria-hidden="true" className="h-full w-full overflow-hidden whitespace-pre-wrap break-words px-3 py-2 font-mono text-[0.625rem] leading-relaxed text-on-surface-low">
      {content.slice(0, 600)}
    </pre>
  )
}

function Placeholder({ tone, label }: { tone: string; label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <span className="rounded-pill px-2.5 py-1 text-[0.6875rem]" style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}>{label}</span>
    </div>
  )
}

function KindTile({ icon: Icon, tone }: { icon: LucideIcon; tone: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center" aria-hidden>
      <span className="grid size-14 place-items-center rounded-xl"
        style={{ background: `color-mix(in srgb, ${tone} 12%, transparent)` }}>
        <Icon size={26} style={{ color: tone }} />
      </span>
    </div>
  )
}

function LivePreview({ art, content, mode }: { art: Artifact; content: string; mode: 'dark' | 'light' }) {
  const srcdoc = useMemo(() => {
    const themeVars = readThemeVars()
    if (art.kind === 'react') return buildReactSrcdoc({ jsx: content, themeVars, mode })
    const html = art.kind === 'svg' ? `<div style="display:grid;place-items:center;height:100vh">${content}</div>` : content
    return buildSrcdoc({ html, themeVars, mode })
  }, [art.kind, content, mode])
  return (
    <div className="pointer-events-none h-full w-full overflow-hidden" aria-hidden>
      <iframe
        srcDoc={srcdoc}
        sandbox="allow-scripts"
        tabIndex={-1}
        title={`Preview of ${art.name}`}
        className="origin-top-left border-none bg-surface"
        style={{ width: `${100 / PREVIEW_SCALE}%`, height: `${100 / PREVIEW_SCALE}%`, transform: `scale(${PREVIEW_SCALE})` }}
      />
    </div>
  )
}

export const ArtifactCard = memo(function ArtifactCard({ art, onOpen }: {
  art: Artifact
  onOpen: (a: Artifact) => void
}) {
  const { mode } = useMode()
  const km = artifactKindMeta(art.kind)
  const rootRef = useRef<HTMLDivElement>(null)
  const [near, setNear] = useState(false)
  const [live, setLive] = useState(false)
  const [content, setContent] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const slotRef = useRef<number | null>(null)

  const ctype = useMemo(() => resolveContentType({ kind: art.kind }), [art.kind])
  const isIframeKind = previewsAsIframe(ctype)
  const isThumbnail = ctype.id === 'image'
  const isKindTile = !!ctype.binary && !isThumbnail
  const isExcerpt = !isIframeKind && !isThumbnail && !isKindTile

  useEffect(() => {
    const el = rootRef.current
    if (!el || typeof IntersectionObserver === 'undefined') { setNear(true); return }
    const obs = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { setNear(true); obs.disconnect() }
    }, { rootMargin: '200px' })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  useEffect(() => {
    if (!near || isThumbnail || isKindTile || content !== null) return
    let alive = true
    api.artifact(art.slug)
      .then((a) => { if (alive) { setContent(a.content ?? ''); setDirty(!!a.live_dirty) } })
      .catch(() => { if (alive) setContent('') })
    return () => { alive = false }
  }, [near, isThumbnail, isKindTile, content, art.slug])

  useEffect(() => {
    if (!near || !isIframeKind || content === null) return
    setLive(true)
    const id = acquireLiveSlot(() => setLive(false))
    slotRef.current = id
    return () => { releaseLiveSlot(id); slotRef.current = null }
  }, [near, isIframeKind, content])


  const preview = (() => {
    if (isKindTile) return <KindTile icon={km.icon} tone={km.tone} />
    if (!near) return <Placeholder tone={km.tone} label={km.label} />
    if (isThumbnail) {
      return <img src={`/api/artifacts/${encodeURIComponent(art.slug)}/raw`} alt={art.name} loading="lazy" className="h-full w-full object-cover" />
    }
    if (content === null) return <Placeholder tone={km.tone} label={km.label} />
    if (isExcerpt) return <ExcerptPreview content={content} />
    if (live) return <LivePreview art={art} content={content} mode={mode} />
    return <Placeholder tone={km.tone} label={km.label} />
  })()

  const Icon = km.icon
  return (
    <div ref={rootRef}>
    <TileButton onClick={() => onOpen(art)} title={art.name} ariaLabel={art.name}
      className="h-full w-full">
      <div className="h-36 w-full shrink-0 overflow-hidden border-b border-outline-variant/30 bg-surface">
        {preview}
      </div>
      <div className="flex min-w-0 flex-col gap-0.5 px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Icon size={13} style={{ color: km.tone }} className="shrink-0" />
          <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{art.name}</span>
        </div>
        <div className="flex items-center gap-1.5 text-on-surface-low text-[0.6875rem]">
          <span>{km.label}</span>
          <span>· v{art.version}</span>
          {art.collection && <span className="truncate rounded-pill bg-surface-high px-1.5">{art.collection}</span>}
          {dirty && <span className="shrink-0 rounded-pill px-1.5" title="The source file changed since the last snapshot" style={{ background: 'color-mix(in srgb, var(--color-warning) 16%, transparent)', color: 'var(--color-warning)' }}>source changed</span>}
          <span className="ml-auto shrink-0">{relTime(art.updated_at || art.created_at)}</span>
        </div>
      </div>
    </TileButton>
    </div>
  )
})
