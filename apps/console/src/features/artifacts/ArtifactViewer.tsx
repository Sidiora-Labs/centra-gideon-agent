import { useEffect, useMemo, useState } from 'react'
import { fvs } from '../../shared/theme/fontWeight'
import {
  Clock, RotateCcw, Loader2, Trash2, FileSymlink, History, Tag, Download, ChevronUp, FileWarning,
  GitCompare, Lock,
} from 'lucide-react'
import { api, ARTIFACT_MODEL_SAVED_EVENT, type Artifact, type ArtifactEvent } from '../../shared/data/api'
import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'
import { isArtifactUpdateFor } from './artifactUpdateSignal'
import { notify } from '../../app/shell/appSdk'
import { confirmDelete } from '../../shared/ui/dialog'
import { Button } from '../../shared/ui/Button'
import { QuietButton } from '../../shared/ui/QuietButton'
import { downloadText, safeFilename } from '../../shared/data/download'
import { artifactKindMeta, relTime } from '../files/fileMeta'
import { ContentSurface } from '../../shared/ui/content/ContentSurface'
import { resolveContentType } from '../../shared/ui/content/contentTypes'
import { useDocumentEditing } from '../../shared/ui/content/documentEditing'
import { ArtifactCompare } from './ArtifactCompare'
import { ArtifactDeploy } from './ArtifactDeploy'
import type { CommentTarget } from '../../shared/ui/content/commentTarget'
import { invalidateKeys } from '../../shared/data/data'
import { ChipInput } from '../../shared/ui/forms'

interface ViewerProps {
  slug: string
  onChanged: () => void
  onDeleted: () => void
  onOpenSourceFile: (path: string) => void
  commentTarget?: CommentTarget
  initialVersion?: number
  onVersionChange?: (v: number | null) => void
  defaultDetailsOpen?: boolean
}

const artifactDrafts = new Map<string, { draft: string; base: string; warned?: boolean }>()

export function ArtifactViewer({ slug, onChanged, onDeleted, onOpenSourceFile, commentTarget, initialVersion, onVersionChange, defaultDetailsOpen = false }: ViewerProps) {
  const [art, setArt] = useState<Artifact | null>(null)
  const [versions, setVersions] = useState<number[]>([])
  const [events, setEvents] = useState<ArtifactEvent[]>([])
  const [selVersion, setSelVersionRaw] = useState<number | null>(initialVersion ?? null)
  const setSelVersion = (v: number | null) => { setSelVersionRaw(v); onVersionChange?.(v) }
  const [viewContent, setViewContent] = useState('')
  const [metaOpen, setMetaOpen] = useState(defaultDetailsOpen)

  const [comparing, setComparing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [tagsBusy, setTagsBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [versionsError, setVersionsError] = useState('')
  const [eventsError, setEventsError] = useState('')
  const [viewError, setViewError] = useState('')

  const reload = async (opts?: { keepVersion?: boolean; quiet?: boolean }) => {
    if (!opts?.quiet) setLoading(true)
    setLoadError('')
    try {
      const [a, v, e] = await Promise.all([
        api.artifact(slug),
        api.artifactVersions(slug).then(
          (r) => { setVersionsError(''); return r.versions },
          (err) => { setVersionsError(String((err as Error)?.message || err)); return null },
        ),
        api.artifactEvents(slug).then(
          (r) => { setEventsError(''); return r.events },
          (err) => { setEventsError(String((err as Error)?.message || err)); return null },
        ),
      ])
      setArt(a); setVersions(v ?? []); setEvents(e ?? [])
      if (!opts?.keepVersion) setSelVersion(null)
      if (!(opts?.keepVersion && selVersion !== null)) setViewContent(a.content ?? '')
    } catch (err) {
      setLoadError(String((err as Error)?.message || err))
    } finally {
      if (!opts?.quiet) setLoading(false)
    }
  }
  useEffect(() => { setComparing(false); reload({ keepVersion: initialVersion != null }) }, [slug])  // eslint-disable-line react-hooks/exhaustive-deps

  useChatSocket((m: WsMessage) => {
    if (!isArtifactUpdateFor(m, slug)) return
    reload({ keepVersion: true, quiet: true }).then(() => onChanged()).catch(() => {})
  })

  useEffect(() => {
    const onModelSaved = (event: Event) => {
      if ((event as CustomEvent<{ slug?: string }>).detail?.slug !== slug) return
      reload({ keepVersion: true, quiet: true }).then(() => onChanged()).catch(() => {})
    }
    window.addEventListener(ARTIFACT_MODEL_SAVED_EVENT, onModelSaved)
    return () => window.removeEventListener(ARTIFACT_MODEL_SAVED_EVENT, onModelSaved)
  }, [slug])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { api.viewRender(`artifact.${slug}`).catch(() => {}) }, [slug])

  useEffect(() => {
    if (selVersion === null) { setViewContent(art?.content ?? ''); setViewError(''); return }
    let alive = true
    setViewError('')
    api.artifactVersion(slug, selVersion)
      .then((a) => { if (alive) setViewContent(a.content ?? '') })
      .catch((e) => { if (alive) { setViewContent(''); setViewError(String((e as Error)?.message || e)) } })
    return () => { alive = false }
  }, [selVersion, slug, art])

  const km = useMemo(() => art ? artifactKindMeta(art.kind) : null, [art])
  const isCurrent = selVersion === null
  const frozen = !!art?.readonly
  const editable = isCurrent && !frozen
  const documentEditing = useDocumentEditing()
  const ctype = useMemo(() => art ? resolveContentType({ kind: art.kind }) : null, [art, documentEditing])

  const onSave = async (draft: string) => {
    if (!art) return
    try {
      await api.updateArtifact(slug, { content: draft, snapshot: false, event_type: 'edited' })
      await reload(); onChanged()
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error'); throw e }
  }
  const snapshot = async (draft: string) => {
    if (!art) return
    try {
      await api.updateArtifact(slug, { content: draft, snapshot: true, event_type: 'iterated' })
      await reload(); onChanged()
    } catch (e) { notify(`Could not snapshot artifact: ${(e as Error).message}`, 'error'); throw e }
  }
  const saveTags = async (tags: string[]) => {
    if (!art || !editable || tagsBusy) return
    const savingSlug = art.slug
    const previous = art.tags
    setTagsBusy(true)
    setArt({ ...art, tags })
    try {
      const updated = await api.updateArtifact(savingSlug, { tags })
      setArt((current) => current?.slug === savingSlug ? updated : current)
      onChanged()
    } catch (e) {
      setArt((current) => current?.slug === savingSlug ? { ...current, tags: previous } : current)
      notify(`Could not save artifact tags: ${(e as Error).message}`, 'error')
    } finally {
      setTagsBusy(false)
    }
  }
  const revert = async () => {
    if (!art || selVersion === null) return
    setBusy(true)
    try {
      await api.updateArtifact(slug, { event_type: 'reverted', from_version: selVersion })
      await reload(); onChanged()
    } catch (e) { notify(`Could not revert: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }
  const del = async () => {
    if (!art) return
    const body = ctype?.binary
      ? 'The image bytes live only here. Any chat message that shows this image will display a "no longer available" placeholder after deletion. Download it first if you want to keep a copy. This cannot be undone.'
      : 'The underlying source file/widget is not touched — only the saved artifact and its version history are removed.'
    if (!(await confirmDelete('artifact', art.name, { body }))) return
    try {
      await api.deleteArtifact(slug)
      invalidateKeys('artifacts:', true)
      onDeleted()
    }
    catch (e) { notify(`Could not delete artifact: ${(e as Error).message}`, 'error') }
  }
  const ext = ({ markdown: 'md', html: 'html', react: 'jsx', svg: 'svg', json: 'json', text: 'txt', widget: 'html', document: 'html', infographic: 'txt' } as Record<string, string>)
  const download = () => {
    if (!art) return
    const suffix = selVersion === null ? '' : `-v${selVersion}`
    if (ctype?.binary) {
      const q = selVersion === null ? '' : `?version=${selVersion}`
      const a = document.createElement('a')
      a.href = `/api/artifacts/${encodeURIComponent(slug)}/raw${q}`
      a.download = `${safeFilename(art.name, art.slug)}${suffix}`
      document.body.appendChild(a); a.click(); a.remove()
      return
    }
    downloadText(`${safeFilename(art.name, art.slug)}${suffix}.${ext[art.kind] || 'txt'}`, viewContent)
  }

  if (loadError && !loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.8125rem]">Couldn't open this artifact.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">It may have been deleted. {loadError}</p>
          <Button variant="ghost-accent" size="xs" onClick={() => reload()} className="mt-1"><RotateCcw size={13} /> Try again</Button>
        </div>
      </div>
    )
  }
  if (loading || !art || !km) return <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
  const Icon = km.icon

  return (
    <div className="flex h-full flex-col">
      {
}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-s border-b border-outline/40 px-m py-2">
          <Icon size={15} style={{ color: km.tone }} className="shrink-0" />
          <span className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{art.name}</span>
          <span className="truncate text-on-surface-low text-[0.75rem] font-mono">{art.slug} · {km.label}</span>
          {art.live_dirty && <span className="shrink-0 rounded px-1.5 py-0.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 18%, transparent)', color: 'var(--color-warning)' }}>source changed</span>}
          <div className="ml-auto flex items-center gap-1">
            {art.source_path && (
              <QuietButton onClick={() => onOpenSourceFile(art.source_path)} title={`Open source file: ${art.source_path}`}>
                <FileSymlink size={13} /> Source file
              </QuietButton>
            )}
            <QuietButton onClick={download} title="Download this artifact">
              <Download size={13} /> Download
            </QuietButton>
            <button onClick={del} type="button" aria-label="Delete artifact" title="Delete artifact" className="inline-flex items-center gap-1 rounded-md px-2 h-7 text-[0.75rem] hover:bg-surface-high" style={{ color: 'var(--color-error)' }}><Trash2 size={13} /></button>
          </div>
        </div>

        {
}
        {isCurrent && <ArtifactDeploy slug={slug} kind={art.kind} />}

        {
}
        {frozen && isCurrent && (
          <div className="flex items-center gap-2 border-b border-outline/40 px-m py-1.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            <Lock size={12} className="text-primary" />
            <span className="text-on-surface-low">Read-only record — a shared chat transcript with credentials redacted. It can be downloaded or deleted, never edited.</span>
          </div>
        )}

        {!isCurrent && viewError && (
          <div data-type="caption" className="flex items-center gap-2 border-b border-outline/40 bg-danger/10 px-m py-1.5">
            <Clock size={12} className="text-danger" />
            {
}
            <span className="text-on-surface-low">Couldn't load v{selVersion} — {viewError}</span>
            <Button variant="ghost" size="xs" onClick={() => setSelVersion(null)} className="ml-auto shrink-0">
              Back to current
            </Button>
          </div>
        )}
        {!isCurrent && !viewError && (
          <div className="flex items-center gap-2 border-b border-outline/40 px-m py-1.5 text-[0.75rem]" style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)' }}>
            <Clock size={12} style={{ color: 'var(--color-warning)' }} />
            <span className="text-on-surface-low">Viewing historical v{selVersion} (read-only)</span>
            {
}
            {!frozen && (
              <button onClick={revert} disabled={busy} type="button" className="ml-auto inline-flex items-center gap-1 rounded-md px-2 h-6 text-[0.75rem]" style={{ color: 'var(--color-warning)', border: '1px solid color-mix(in srgb, var(--color-warning) 35%, transparent)' }}>
                <RotateCcw size={11} /> Revert to v{selVersion}
              </button>
            )}
          </div>
        )}

        {
}
        <div className="min-h-0 flex-1">
          {comparing
            ? <ArtifactCompare art={art} versions={versions} />
            : ctype && (
            <ContentSurface
              key={`${art.slug}:${selVersion ?? 'cur'}`}
              type={ctype}
              content={viewContent}
              title={art.name}
              docId={art.slug}
              path={art.source_path || undefined}
              readOnly={!editable}
              draftStore={editable ? artifactDrafts : undefined}
              onSave={editable ? onSave : undefined}
              iterate={{ slug: art.slug, persistVersion: editable ? snapshot : undefined }}
              commentTarget={commentTarget}
              actions={editable ? [{ icon: History, label: 'Snapshot', title: 'Save as a new version snapshot', primary: true, run: snapshot }] : undefined}
            />
          )}
        </div>
      </div>

      {
}
      <div className="shrink-0 border-t border-outline/40 bg-surface-container/40">
        <button type="button" onClick={() => setMetaOpen((v) => !v)} aria-expanded={metaOpen}
          className="flex w-full items-center gap-2 px-m py-2 text-on-surface-low hover:text-on-surface transition-colors">
          <ChevronUp size={14} className={`transition-transform ${metaOpen ? '' : 'rotate-180'}`} />
          <span className="text-[0.75rem] uppercase tracking-wide">Details</span>
          <span className="text-on-surface-low text-[0.75rem]">· v{art.version}{art.tags.length ? ` · ${art.tags.length} tag${art.tags.length === 1 ? '' : 's'}` : ''} · {events.length} event{events.length === 1 ? '' : 's'}</span>
        </button>
        {metaOpen && (
          <div className="grid max-h-[40vh] grid-cols-1 gap-l overflow-y-auto px-m pb-m sm:grid-cols-3">
            <div>
              <Label icon={History}>Versions</Label>
              {
}
              {versionsError ? (
                <div className="mt-1.5 flex flex-col items-start gap-1">
                  <span className="flex items-center gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-error)' }}>
                    <FileWarning size={12} /> Couldn't load version history.
                  </span>
                  <span className="text-on-surface-low text-[0.75rem]">{versionsError}</span>
                  <QuietButton onClick={() => reload({ keepVersion: true })} title="Retry loading the version history">
                    <RotateCcw size={13} /> Try again
                  </QuietButton>
                </div>
              ) : versions.length === 0 ? (
                <span className="mt-1.5 block text-on-surface-low text-[0.75rem]">No version history.</span>
              ) : (
              <select value={selVersion ?? 'current'} onChange={(e) => setSelVersion(e.target.value === 'current' ? null : Number(e.target.value))}
                aria-label="Version"
                className="mt-1.5 h-8 w-full rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
                <option value="current">Current · v{art.version}</option>
                {versions.slice().reverse().filter((v) => v !== art.version).map((v) => <option key={v} value={v}>v{v}</option>)}
              </select>
              )}
              {
}
              {versions.length > 1 && (
                <QuietButton onClick={() => setComparing((v) => !v)} ariaExpanded={comparing}
                  title={comparing ? 'Close the version comparison' : 'Compare two versions of this artifact'}
                  className="mt-1.5">
                  <GitCompare size={13} /> {comparing ? 'Close compare' : 'Compare versions'}
                </QuietButton>
              )}
            </div>

            <div>
              <Label icon={Tag}>Tags</Label>
              {editable ? (
                <div className="mt-1.5">
                  <ChipInput values={art.tags} onChange={saveTags} placeholder="Add a tag, Enter"
                    ariaLabel="Artifact tags" disabled={tagsBusy} disabledReason="Saving tags" />
                </div>
              ) : (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {art.tags.length ? art.tags.map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low text-[0.75rem]">{t}</span>)
                    : <span className="text-on-surface-low text-[0.75rem]">None</span>}
                </div>
              )}
            </div>

            <div>
              <Label icon={Clock}>Timeline</Label>
              <div className="mt-2 flex flex-col gap-2.5">
                {
}
                {eventsError && (
                  <span className="flex items-center gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-error)' }}>
                    <FileWarning size={12} /> Couldn't load the timeline.
                  </span>
                )}
                {!eventsError && events.length === 0 && <span className="text-on-surface-low text-[0.75rem]">No events.</span>}
                {events.slice().reverse().map((e, i) => (
                  <div key={i} className="flex items-start gap-2 text-[0.75rem]">
                    <span className="mt-1 size-1.5 shrink-0 rounded-full" style={{ background: eventTone(e.type) }} />
                    <div className="min-w-0">
                      <div className="text-on-surface">
                        {e.type}{e.type === 'reverted' && e.from_version ? ` v${e.from_version}→v${e.version}` : e.version ? ` (v${e.version})` : ''}
                      </div>
                      <div className="text-on-surface-low">{e.by || 'system'} · {relTime(e.ts)}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Label({ icon: Icon, children }: { icon: typeof Clock; children: React.ReactNode }) {
  return <div className="flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide"><Icon size={11} /> {children}</div>
}

function eventTone(type: string): string {
  if (type === 'created') return 'var(--color-success)'
  if (type === 'reverted') return 'var(--color-warning)'
  if (type === 'referenced') return 'var(--color-on-surface-low)'
  return 'var(--color-primary)'
}
