import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react'
import { fvs } from '../../../shared/theme/fontWeight'
import { Download, Loader2, BookmarkPlus, FileWarning, RotateCcw, FolderOpen } from 'lucide-react'
import { api, type FsEntry } from '../../../shared/data/api'
import { mergeText } from '../../../shared/data/staleWrite'
import { useStaleWriteGuard } from '../../../shared/data/useStaleWriteGuard'
import { HeldChange, StaleWriteNotice } from '../../../shared/ui/StaleWriteNotice'
import { useIsMac } from '../../../app/shell/usePlatform'
import { confirm } from '../../../shared/ui/dialog'
import { fmtBytes, baseName, monacoLang } from '../fileMeta'
import { useFileWatch } from './useFileWatch'
import { ContentSurface, type ContentSurfaceHandle } from '../../../shared/ui/content/ContentSurface'
import type { DraftAuthority } from '../../../shared/ui/content/contentSurfaceState'
import { SquareIconButton } from '../../../shared/ui/SquareIconButton'
import { QuietButton } from '../../../shared/ui/QuietButton'
import { Button } from '../../../shared/ui/Button'
import { TextLink } from '../../../shared/ui/TextLink'
import { Centered } from '../../../shared/ui/Centered'
import { resolveContentType, getContentType } from '../../../shared/ui/content/contentTypes'
import type { CommentTarget } from '../../../shared/ui/content/commentTarget'

export interface FileViewerHandle { save: () => void }

interface ViewerProps {
  entry: FsEntry
  onSaved: (content?: string, sourceRevision?: string) => void
  onSaveAsArtifact: (entry: FsEntry, content: string, sourceRevision?: string) => void
  onDirtyChange?: (dirty: boolean) => void
  draftStore?: Map<string, { draft: string; base: string; revision?: string; validator?: string; warned?: boolean }>
  compact?: boolean
  commentTarget?: CommentTarget
  onMissing?: (path: string) => void
}

export const FileViewer = forwardRef<FileViewerHandle, ViewerProps>(function FileViewer(
  { entry, onSaved, onSaveAsArtifact, onDirtyChange, draftStore, compact = false, commentTarget, onMissing }, ref,
) {
  const surfaceRef = useRef<ContentSurfaceHandle>(null)
  const isMac = useIsMac()
  const lang = monacoLang(entry.name)
  const resolved = resolveContentType({ name: entry.name })
  const isCodeFile = (resolved.id === 'text') && lang !== 'plaintext'
  const type = isCodeFile ? (getContentType('code') ?? resolved) : resolved
  const isBinaryType = !!type.binary

  const onMissingRef = useRef(onMissing)
  onMissingRef.current = onMissing

  const [content, setContent] = useState<string | null>(null)
  const [sourceRevision, setSourceRevision] = useState<string | undefined>()
  const [revision, setRevision] = useState<string | undefined>()
  const [contentPath, setContentPath] = useState(isBinaryType ? entry.path : '')
  const [draft, setDraft] = useState('')
  const [truncated, setTruncated] = useState(false)
  const [detectedBinary, setDetectedBinary] = useState(false)
  const [loading, setLoading] = useState(!isBinaryType)
  const [err, setErr] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [diskChanged, setDiskChanged] = useState(false)
  const authorities = useRef(new Map<string, string | undefined>())
  const currentAuthority = useRef<{ content: string; revision: string; validator?: string } | null>(null)
  const savedAuthority = useRef<{ content: string; revision: string; validator?: string } | null>(null)

  useEffect(() => {
    if (isBinaryType) { setContent(null); setContentPath(entry.path); setLoading(false); return }
    let alive = true
    setContent(null); setSourceRevision(undefined); setRevision(undefined); setLoading(true); setErr(''); setDetectedBinary(false)
    api.fileRead(entry.path, true).then((r) => {
      if (!alive) return
      if (r.binary) { setDetectedBinary(true); setContent(''); setContentPath(entry.path); setDraft(''); setLoading(false); return }
      const cached = draftStore?.get(entry.path)
      setContent(r.content); setSourceRevision(r.source_revision); setRevision(r.revision); setContentPath(entry.path); setDraft(cached ? cached.draft : r.content); setTruncated(r.truncated); setLoading(false)
      if (r.revision) authorities.current.set(JSON.stringify([entry.path, r.revision]), r.source_revision)
      if (cached?.revision && cached.validator) authorities.current.set(JSON.stringify([entry.path, cached.revision]), cached.validator)
      if (r.revision) currentAuthority.current = { content: r.content, revision: r.revision, validator: r.source_revision }
      if (cached) setDiskChanged(cached.draft !== r.content && (cached.warned || cached.base !== r.content))
      else setDiskChanged(false)
    }).catch((e) => {
      if (!alive) return
      if ((e as { status?: number }).status === 404) onMissingRef.current?.(entry.path)
      setContentPath(entry.path); setErr(String(e.message || e)); setLoading(false)
    })
    return () => { alive = false }
  }, [entry.path, isBinaryType, attempt])

  const stale = useStaleWriteGuard<string>({
    read: async () => {
      const current = await api.fileRead(entry.path, true)
      if (!current.revision) throw new Error('The current file has no revision. Reload it before saving.')
      authorities.current.set(JSON.stringify([entry.path, current.revision]), current.source_revision)
      currentAuthority.current = { content: current.content, revision: current.revision, validator: current.source_revision }
      return { value: current.content, revision: current.revision }
    },
    write: async (next, basedOn) => {
      const expectedValidator = authorities.current.get(JSON.stringify([entry.path, basedOn]))
      if (!expectedValidator) throw new Error('The source bytes could not be verified for this draft. Refresh and rebase it before saving.')
      await api.fileWrite(entry.path, next, basedOn, expectedValidator)
      const current = await api.fileRead(entry.path, true)
      if (!current.revision || !current.source_revision) throw new Error('The saved file has no complete version. Reload it before continuing.')
      savedAuthority.current = { content: current.content, revision: current.revision, validator: current.source_revision }
      currentAuthority.current = savedAuthority.current
      authorities.current.set(JSON.stringify([entry.path, current.revision]), current.source_revision)
      setRevision(current.revision); setSourceRevision(current.source_revision); setDiskChanged(false)
    },
    onSaved: () => {
      const current = savedAuthority.current
      if (!current) return
      setContent(current.content); setDraft(current.content); setRevision(current.revision); setSourceRevision(current.validator)
      onSaved(current.content, current.validator)
    },
    onDiscard: () => {
      const current = currentAuthority.current
      if (!current) return
      setContent(current.content); setDraft(current.content); setRevision(current.revision); setSourceRevision(current.validator); setDiskChanged(false)
    },
  })

  useFileWatch(entry.path, !isBinaryType, (next) => {
    setDraft((d) => { if (content !== null && d !== content && d !== next) setDiskChanged(true); return d })
  })

  const pathLoading = contentPath !== entry.path
  const loadFailed = content === null && !loading && !pathLoading && !isBinaryType && !!err
  const noText = isBinaryType || detectedBinary || loadFailed

  const save = async () => surfaceRef.current?.save()
  useImperativeHandle(ref, () => ({ save }))

  const onSurfaceSave = async (next: string, base: DraftAuthority) => {
    setErr('')
    try {
      if (!base.revision || !base.validator) throw new Error('This file draft has no complete version. Refresh and rebase it before saving.')
      const saved = await stale.save({ value: base.content, revision: base.revision }, next, (theirs) => mergeText(base.content, next, theirs))
      if (!saved) throw new Error('The file changed elsewhere. Review the retained change below before saving again.')
      const current = savedAuthority.current
      if (!current) throw new Error('The saved file could not be reread. Reload it before continuing.')
      setContent(current.content); setDraft(current.content); setRevision(current.revision); setSourceRevision(current.validator)
    }
    catch (e) { setErr(String((e as Error).message || e)); throw e }
  }
  const onRebased = (authority: DraftAuthority) => {
    if (!authority.revision || !authority.validator) return
    authorities.current.set(JSON.stringify([entry.path, authority.revision]), authority.validator)
    currentAuthority.current = { content: authority.content, revision: authority.revision, validator: authority.validator }
    setContent(authority.content); setRevision(authority.revision); setSourceRevision(authority.validator); setDiskChanged(false)
  }
  const confirmSave = async () => {
    if (!diskChanged) return true
    return confirm({
      title: 'File changed on disk',
      body: 'This file changed on disk (another process — or an agent — may have rewritten it) while you were editing. '
        + 'Saving will overwrite those changes with your version. Save anyway?',
      danger: true,
      confirmLabel: 'Overwrite',
    })
  }

  const fileName = baseName(entry.path)

  if (loading || pathLoading) return <Centered><Loader2 size={20} className="animate-spin text-on-surface-low" /></Centered>
  if (loadFailed) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.8125rem]">Couldn't open this file.</p>
          <p className="text-[0.75rem] text-on-surface-low/80">{err}</p>
          <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)} className="mt-1"><RotateCcw size={13} /> Try again</Button>
        </div>
      </Centered>
    )
  }
  if (detectedBinary) {
    return (
      <Centered>
        <div className="flex flex-col items-center gap-2 text-on-surface-low">
          <FileWarning size={26} className="opacity-40" />
          <p className="text-[0.8125rem]">This looks like a binary file — it can't be shown as text.</p>
          <TextLink href={api.fileRawUrl(entry.path, true)} external size="sm">Download to inspect it</TextLink>
        </div>
      </Centered>
    )
  }

  const headerLeft = (
    <>
      <span className="truncate text-on-surface text-[0.8125rem] font-mono" style={fvs(500)}>{fileName}</span>
      {diskChanged && !noText && (
        <span className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
          title="The file changed on disk (another process — or an agent — rewrote it) while you were editing. Saving overwrites those changes; Revert to take the disk version.">
          <FileWarning size={11} /> changed on disk
        </span>
      )}
      {!compact && <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{fmtBytes(entry.size)}</span>}
    </>
  )
  const headerExtras = (
    <>
      {!noText && !truncated && content !== null && (
        compact
          ? <SquareIconButton icon={BookmarkPlus} iconSize={13} label="Save as a versioned artifact" onClick={() => onSaveAsArtifact(entry, draft, sourceRevision)} />
          : <QuietButton onClick={() => onSaveAsArtifact(entry, draft, sourceRevision)} title="Save as a versioned artifact">
              <BookmarkPlus size={13} /> Artifact
            </QuietButton>
      )}
      { }
      {isMac && (
        <SquareIconButton icon={FolderOpen} iconSize={13} label="Reveal in Finder" onClick={() => { void api.revealPath(entry.path, 'reveal').catch(() => {}) }} />
      )}
      <a href={api.fileRawUrl(entry.path, true)} download={fileName} target="_blank" rel="noreferrer"
        className="inline-flex size-7 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface" title="Download"><Download size={13} /></a>
    </>
  )
  const banner = err && !loadFailed
    ? <div className="mx-m mt-2 flex items-center gap-2 rounded-md px-3 py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-error) 12%, transparent)', color: 'var(--color-error)' }}><FileWarning size={14} /> {err}</div>
    : null

  return (
    <div className="contents">
    <StaleWriteNotice guard={stale} what="This file" />
    <HeldChange guard={stale}>
    <ContentSurface
      ref={surfaceRef}
      type={type}
      content={isBinaryType ? '' : (content ?? '')}
      title={entry.name}
      docId={entry.path}
      path={['docx', 'xlsx', 'pptx', 'video'].includes(type.id) ? api.fileRawUrl(entry.path, true) : entry.path}
      language={lang}
      readOnly={noText}
      truncated={truncated && !noText}
      onSave={noText ? undefined : onSurfaceSave}
      confirmSave={confirmSave}
      onDirtyChange={onDirtyChange}
      onDraftChange={(d) => setDraft(d)}
      commentTarget={commentTarget}
      compact={compact}
      draftStore={draftStore}
      revision={revision}
      validator={sourceRevision}
      requireRevision
      requireValidator
      readCurrent={async () => {
        const current = await api.fileRead(entry.path, true)
        return { content: current.content, revision: current.revision, validator: current.source_revision }
      }}
      onRebased={onRebased}
      headerLeft={headerLeft}
      headerExtras={headerExtras}
      banner={banner}
    />
    </HeldChange>
    </div>
  )
})
