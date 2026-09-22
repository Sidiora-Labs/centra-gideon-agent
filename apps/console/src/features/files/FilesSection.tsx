import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Box, Search, FilePlus2, FolderPlus, RefreshCw, GitBranch, Files as FilesIcon, X, Loader2, CornerDownRight, PanelRight,
} from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { SidePanel } from '../../shared/ui/SidePanel'
import { EmptyState, Loading } from '../../shared/ui/ListScaffold'
import { Modal } from '../../shared/ui/Modal'
import { SearchField } from '../../shared/ui/SearchField'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { Segmented, TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { Meter } from '../../shared/ui/Meter'
import { api, type Artifact, type FsEntry, type ContentMatch } from '../../shared/data/api'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { confirm } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { useFileRoots, useDirCache, useGitStatus } from './filesData'
import { FileTree } from './browse/FileTree'
import { FileViewer, type FileViewerHandle } from './browse/FileViewer'
import { PathBar } from './browse/PathBar'
import { useFileTabs } from './browse/useFileTabs'
import { newSessionTarget } from '../../shared/ui/content/commentTarget'
import { baseName, fileIcon } from './fileMeta'
import { PageTitle } from '../../shared/ui/PageTitle'
import { tabListKeys } from '../../shared/data/tabListKeys'

const TAB_KEY = 'files-tab'

export function FilesSection({ sub, navigate, query: routeQuery, setQuery }: RouteProps) {
  const { roots, loading: rootsLoading } = useFileRoots()
  const deepSlug = (sub || '').split('/')[0] || ''
  useEffect(() => {
    if (deepSlug) navigate(`artifacts/${deepSlug}`, { replace: true })
  }, [deepSlug, navigate])
  const [dir, setDir] = useQueryParam(routeQuery, setQuery, 'dir', '')
  const [requestedFile] = useQueryParam(routeQuery, setQuery, 'file', '')
  const [tab, setTab] = useState<string>(() => localStorage.getItem(TAB_KEY) || '')
  useEffect(() => { if (tab) localStorage.setItem(TAB_KEY, tab) }, [tab])

  const activeRoot = dir || tab
  const switchTab = useCallback((t: string) => {
    setTab(t)
    if (dir) setQuery({ dir: null }, { replace: true })
  }, [dir, setQuery])

  const dirs = useDirCache()
  const [nonce, setNonce] = useState(0)
  const { branch, statuses } = useGitStatus(activeRoot || null, nonce)

  const fileTabs = useFileTabs()
  const viewerRefs = useRef(new Map<string, FileViewerHandle>())
  const draftStore = useRef(new Map<string, { draft: string; base: string; warned?: boolean }>()).current
  const closeTab = useCallback(async (path: string) => {
    if (await fileTabs.close(path)) draftStore.delete(path)
  }, [fileTabs, draftStore])

  const [grep, setGrep] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [include, setInclude] = useQueryParam(routeQuery, setQuery, 'include', '', { replace: true })
  const [results, setResults] = useState<ContentMatch[]>([])
  const [searchEngine, setSearchEngine] = useState<'rg' | 'python' | ''>('')
  const [searchBusy, setSearchBusy] = useState(false)
  const [searchErr, setSearchErr] = useState<unknown>(null)

  const [creating, setCreating] = useState<null | 'file' | 'dir'>(null)
  const [newName, setNewName] = useState('')
  const [fileErr, setFileErr] = useState<string | null>(null)
  const [uploadRows, setUploadRows] = useState<{ name: string; pct: number }[]>([])
  const uploadAbortRef = useRef<AbortController | null>(null)
  const [rootDrop, setRootDrop] = useState(false)
  const [artModal, setArtModal] = useState<{ entry: FsEntry; content: string; name: string } | null>(null)

  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [explorerOpen, setExplorerOpen] = useState(true)

  useEffect(() => {
    if (!dir || !roots.length) return
    const owning = roots.filter((r) => dir === r.path || dir.startsWith(r.path.replace(/\/$/, '') + '/'))
      .sort((a, b) => b.path.length - a.path.length)[0]
    if (owning && owning.path !== tab) setTab(owning.path)
  }, [dir, roots]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!roots.length) return
    if (!roots.some((r) => r.path === tab)) setTab(roots[0].path)
  }, [roots, tab])

  const loadArtifacts = useCallback(async () => {
    try { setArtifacts(await api.artifacts()) } catch {   }
  }, [])
  useEffect(() => { loadArtifacts() }, [loadArtifacts])

  const artifactPaths = useMemo(() => new Set(artifacts.filter((a) => a.source_path).map((a) => a.source_path)), [artifacts])

  const refresh = useCallback(() => { dirs.invalidate(activeRoot); setNonce((n) => n + 1) }, [dirs, activeRoot])

  const openByPath = useCallback((path: string, rootPath?: string) => {
    if (rootPath) setTab(rootPath)
    fileTabs.open({ name: baseName(path), path, is_dir: false })
  }, [fileTabs.open])

  useEffect(() => {
    if (requestedFile) openByPath(requestedFile)
  }, [requestedFile, openByPath])

  useEffect(() => {
    if (!activeRoot || grep.trim().length < 2) { setResults([]); setSearchEngine(''); setSearchErr(null); return }
    setSearchBusy(true)
    const t = setTimeout(() => {
      api.fileContentSearch(activeRoot, grep.trim(), include.trim() || undefined)
        .then((r) => { setResults(r.results); setSearchEngine(r.engine); setSearchErr(null) })
        .catch((e) => { setResults([]); setSearchEngine(''); setSearchErr(e) })
        .finally(() => setSearchBusy(false))
    }, 250)
    return () => clearTimeout(t)
  }, [grep, include, activeRoot])

  const searchRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      const k = e.key.toLowerCase()
      if (k === 's' && fileTabs.activePath) { e.preventDefault(); viewerRefs.current.get(fileTabs.activePath)?.save() }
      else if (k === 'f') { e.preventDefault(); searchRef.current?.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fileTabs.activePath])

  const saveAsArtifact = (entry: FsEntry, content: string) => setArtModal({ entry, content, name: baseName(entry.path) })
  const confirmArtifact = async () => {
    if (!artModal || !artModal.name.trim()) return
    try {
      const created = await api.createArtifact({ name: artModal.name.trim(), content: artModal.content, source: 'manual', source_path: artModal.entry.path, kind: guessKind(artModal.entry.name) })
      setArtModal(null); await loadArtifacts()
      navigate(`artifacts/${created.slug}`)
    } catch (e) { notify(`Could not save artifact: ${(e as Error).message}`, 'error') }
  }

  const submitCreate = async () => {
    const name = newName.trim()
    if (!name || !creating || !activeRoot) { setCreating(null); setNewName(''); return }
    try {
      const r = await api.fileCreate(activeRoot, name, creating)
      setCreating(null); setNewName(''); refresh()
      if (creating === 'file') fileTabs.open({ name, path: r.path, is_dir: false })
    } catch (e) { notify(`Could not create: ${(e as Error).message}`, 'error') }
  }

  const onRename = useCallback(async (entry: FsEntry, nextName: string) => {
    const parent = entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '')
    const dest = parent ? `${parent}/${nextName}` : nextName
    setFileErr(null)

    const moved = (p: string) => (p === entry.path ? dest : p.startsWith(`${entry.path}/`) ? dest + p.slice(entry.path.length) : p)

    try {
      await api.fileMove(entry.path, dest)
      for (const [key, val] of [...draftStore]) {
        const next = moved(key)
        if (next !== key) { draftStore.delete(key); draftStore.set(next, val) }
      }
      fileTabs.renamePath(entry.path, dest)
      refresh()
    } catch (e) {
      setFileErr(`Rename failed: ${(e as Error).message}`)
    }
  }, [fileTabs, refresh, draftStore])

  const onDelete = useCallback(async (entry: FsEntry) => {
    const ok = await confirm({
      title: entry.is_dir ? `Delete folder "${entry.name}"?` : `Delete file "${entry.name}"?`,
      body: entry.is_dir ? 'This deletes the folder and all its contents. This cannot be undone.' : 'This cannot be undone.',
      danger: true,
      confirmLabel: 'Delete',
    })
    if (!ok) return
    setFileErr(null)
    try {
      await api.fileDelete(entry.path)
      if (fileTabs.tabs.some((t) => t.path === entry.path)) void closeTab(entry.path)
      refresh()
    } catch (e) { setFileErr(`Delete failed: ${(e as Error).message}`) }
  }, [fileTabs, refresh])

  const onUpload = useCallback(async (dir: string, files: File[]) => {
    if (!files.length) return
    setFileErr(null)
    const { precheck } = await import('../../shared/data/chunkedUpload')
    const ok: File[] = []
    for (const f of files) {
      const err = await precheck(f)
      if (err) setFileErr(err)
      else ok.push(f)
    }
    if (!ok.length) return
    setUploadRows(ok.map((f) => ({ name: f.name, pct: 0 })))
    const ctrl = new AbortController()
    uploadAbortRef.current = ctrl
    try {
      const r = await api.fileUpload(dir, ok, (idx, p) => {
        setUploadRows((prev) => prev.map((u, i) => (i === idx ? { ...u, pct: p.pct } : u)))
      }, ctrl.signal)
      if (r.ok) refresh()
      else setFileErr(`Upload failed: ${r.error ?? 'unknown error'}`)
    } catch (e) {
      const { isAbortError } = await import('../../shared/data/chunkedUpload')
      if (!isAbortError(e)) setFileErr(`Upload failed: ${(e as Error).message}`)
    } finally {
      uploadAbortRef.current = null
      setUploadRows([])
    }
  }, [refresh])

  const tabOptions = roots.map((r) => ({ key: r.path, label: r.label }))
  const showResults = grep.trim().length >= 2
  const activeFile = fileTabs.active

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <PageTitle className="shrink-0">Files</PageTitle>
          {!rootsLoading && roots.length > 0 && <div className="min-w-0 overflow-x-auto"><Segmented ariaLabel="File root" value={tab} onChange={switchTab} options={tabOptions} /></div>}
        </div>}
        right={
          <HeaderActions>
            <HeaderControl icon={PanelRight}
              label={explorerOpen ? 'Hide explorer' : 'Show explorer'}
              active={explorerOpen} onClick={() => setExplorerOpen((v) => !v)} />
          </HeaderActions>
        }
      />

      {
}
      <div className="mx-auto flex min-h-0 w-full flex-1" style={{ maxWidth: 'var(--content-width)' }}>
            {
}
            <div className="flex min-w-0 flex-1 flex-col">
              {fileTabs.tabs.length > 0 && (
                <div role="tablist" aria-label="Open files"
                  onKeyDown={tabListKeys((i) => fileTabs.setActivePath(fileTabs.tabs[i].path))}
                  className="flex items-stretch gap-1 overflow-x-auto border-b border-outline/40 px-2 pt-2">
                  {fileTabs.tabs.map((t) => {
                    const Icon = fileIcon(t.name, false)
                    const on = t.path === fileTabs.activePath
                    return (
                      <div key={t.path} role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
                        onClick={() => fileTabs.setActivePath(t.path)} title={t.path}
                        aria-keyshortcuts="Delete"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileTabs.setActivePath(t.path); return }
                          if (e.key === 'Delete') { e.preventDefault(); void closeTab(t.path) }
                        }}
                        className="group/tab inline-flex h-10 shrink-0 cursor-pointer items-center gap-2 rounded-t-lg border border-b-0 pl-3.5 pr-2 text-[0.8125rem] transition-colors"
                        style={on ? { background: 'var(--color-surface-container)', color: 'var(--color-on-surface)', borderColor: 'var(--color-outline)' } : { color: 'var(--color-on-surface-low)', borderColor: 'transparent' }}>
                        <Icon size={14} className="shrink-0 opacity-70" />
                        <span className="max-w-[180px] truncate">{t.name}</span>
                        {fileTabs.dirty[t.path] && <span className="size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} />}
                        <button type="button" onClick={(e) => { e.stopPropagation(); void closeTab(t.path) }}
                          aria-label={`Close ${t.name}`} title="Close file"
                          className="grid size-6 -mr-0.5 shrink-0 place-items-center rounded opacity-50 hover:bg-surface-high hover:opacity-100"><X size={13} /></button>
                      </div>
                    )
                  })}
                </div>
              )}
              <div className="relative min-h-0 flex-1">
                {!activeFile ? (
                  <EmptyState icon={FilesIcon} title="No file open" hint="Pick a file from the explorer to view or edit it. Type in the search box to grep contents (⌘F)." />
                ) : (
                  <div className="absolute inset-0">
                      <FileViewer key={activeFile.path}
                        ref={(h) => { if (h) viewerRefs.current.set(activeFile.path, h); else viewerRefs.current.delete(activeFile.path) }}
                        entry={{ name: activeFile.name, path: activeFile.path, is_dir: false }} onSaved={refresh} onSaveAsArtifact={saveAsArtifact}
                        onDirtyChange={(d) => fileTabs.markDirty(activeFile.path, d)} onMissing={(p) => { draftStore.delete(p); fileTabs.closeNow(p) }}
                        draftStore={draftStore}
                        commentTarget={navigate ? newSessionTarget(navigate, { name: `Comments: ${activeFile.name}` }) : undefined} />
                  </div>
                )}
              </div>
            </div>

            { }
            {explorerOpen && (
            <SidePanel title="Explorer" icon={<FilesIcon size={18} />} storeKey="files-explorer-w" fillHeight onClose={() => setExplorerOpen(false)}>
            <div className="flex h-full flex-col">
              <div className="flex flex-col gap-2 border-b border-outline/40 p-m">
                <SearchField inputRef={searchRef} value={grep} onChange={setGrep} size="sm"
                  placeholder="Search contents…  ⌘F" name="workspace-grep" ariaLabel="Search file contents" />
                {
}
                <ResultAnnouncement count={results.length} noun="matches" singular="match" empty="No matches" active={showResults} />
                {showResults && (
                  <input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="include glob e.g. *.py"
                    name="workspace-grep-include" aria-label="Restrict search to files matching glob"
                    className="h-7 w-full rounded-md bg-surface-high px-2.5 text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
                )}
                {!showResults && (
                  <>
                    { }
                    <PathBar value={dirs.resolved[activeRoot] || activeRoot} onNavigate={setDir} />
                    { }
                    <div className="flex items-center gap-0.5">
                      <RailBtn icon={FilePlus2} label="New file" onClick={() => { setCreating('file'); setNewName('') }} />
                      <RailBtn icon={FolderPlus} label="New folder" onClick={() => { setCreating('dir'); setNewName('') }} />
                      <RailBtn icon={RefreshCw} label="Refresh" onClick={refresh} />
                    </div>
                  </>
                )}
                {(branch || searchEngine) && (
                  <div className="flex items-center gap-2 text-on-surface-low text-[0.75rem]">
                    {showResults
                      ? <span>{searchBusy ? 'searching…' : `${results.length} match${results.length === 1 ? '' : 'es'}`}{searchEngine && ` · ${searchEngine === 'rg' ? 'ripgrep' : 'Python fallback'}`}</span>
                      : branch && <span className="inline-flex items-center gap-1"><GitBranch size={11} /> {branch}</span>}
                  </div>
                )}
              </div>

              {fileErr && (
                <InlineError className="mx-m mt-2" onDismiss={() => setFileErr(null)}>{fileErr}</InlineError>
              )}
              {uploadRows.length > 0 && (
                <div className="mx-m mt-2 flex flex-col gap-1 rounded-lg bg-surface-container/60 px-3 py-2">
                  {uploadRows.map((u) => (
                    <div key={u.name} className="flex items-center gap-2.5 text-[0.75rem] text-on-surface-var">
                      <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
                      <span className="max-w-[40%] shrink-0 truncate" title={u.name}>{u.name}</span>
                      <Meter size="thin" className="min-w-0 flex-1" label={`Uploading ${u.name}`} pct={u.pct} />
                      <span className="shrink-0 tabular-nums text-on-surface-low">{u.pct}%</span>
                      <button type="button" aria-label="Cancel upload"
                        className="shrink-0 rounded p-0.5 text-on-surface-low hover:text-danger"
                        onClick={() => uploadAbortRef.current?.abort()}>
                        <X size={13} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="min-h-0 flex-1 overflow-y-auto py-1"
                onDragOver={!showResults && activeRoot ? (e) => { e.preventDefault(); setRootDrop(true) } : undefined}
                onDragLeave={() => setRootDrop(false)}
                onDrop={!showResults && activeRoot ? (e) => {
                  e.preventDefault(); setRootDrop(false)
                  const files = Array.from(e.dataTransfer.files)
                  if (files.length) onUpload(activeRoot, files)
                } : undefined}
                style={rootDrop ? { boxShadow: 'inset 0 0 0 2px var(--color-primary)' } : undefined}>
                {creating && (
                  <div className="flex items-center gap-1.5 px-m py-1.5">
                    {creating === 'dir' ? <FolderPlus size={14} className="text-primary" /> : <FilePlus2 size={14} className="text-primary" />}
                    {
}
                    <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') { setCreating(null); setNewName('') } }}
                      onBlur={submitCreate} placeholder={creating === 'dir' ? 'folder name' : 'file name'}
                      aria-label={creating === 'dir' ? 'New folder name' : 'New file name'}
                      className="h-7 flex-1 rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
                  </div>
                )}
                {showResults
                  ? <SearchResults results={results} busy={searchBusy} error={searchErr} onOpen={(m) => openByPath(m.file)} />
                  : activeRoot
                    ? <FileTree key={`${activeRoot}:${nonce}`} dirs={dirs} rootPath={activeRoot} activePath={fileTabs.activePath || null}
                        gitStatuses={statuses} onOpenFile={fileTabs.open} artifactPaths={artifactPaths}
                        onRename={onRename} onDelete={onDelete} onUpload={(entry, files) => onUpload(entry.path, files)} />
                    : <Loading what="the file" />}
              </div>
            </div>
            </SidePanel>
            )}
      </div>

      {artModal && (
        <Modal title="Save as artifact" icon={<Box size={18} className="text-primary" />} onClose={() => setArtModal(null)}>
          <div className="flex flex-col gap-m p-l" style={{ minWidth: 360 }}>
            <p className="text-on-surface-low text-[0.8125rem]">Creates a versioned artifact that live-points at <span className="font-mono">{baseName(artModal.entry.path)}</span>. Re-saving bumps it instead of duplicating.</p>
            <TextInput value={artModal.name} onChange={(v) => setArtModal((m) => m && { ...m, name: v })} placeholder="Artifact name" autoFocus />
            <div className="flex justify-end gap-s">
              <Button variant="ghost" size="sm" onClick={() => setArtModal(null)}>Cancel</Button>
              <Button size="sm" onClick={confirmArtifact}>Save artifact</Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

function SearchResults({ results, busy, error, onOpen }: { results: ContentMatch[]; busy: boolean; error?: unknown; onOpen: (m: ContentMatch) => void }) {
  if (busy && results.length === 0) return <div className="flex items-center justify-center py-8"><Loader2 size={18} className="animate-spin text-on-surface-low" /></div>
  if (error && results.length === 0) {
    return (
      <div role="alert" className="px-m py-s text-danger text-[0.8125rem]">
        Search failed{(error as Error)?.message ? `: ${(error as Error).message}` : '.'}
      </div>
    )
  }
  if (results.length === 0) return <div className="px-m py-s text-on-surface-low text-[0.8125rem]">No matches.</div>
  return (
    <div className="flex flex-col">
      {results.map((m, i) => (
        <button key={i} onClick={() => onOpen(m)} type="button" className="flex w-full flex-col items-start gap-0.5 rounded-md px-m py-1.5 text-left transition-colors hover:bg-surface-high">
          <span className="flex items-center gap-1 text-on-surface text-[0.8125rem] font-mono"><CornerDownRight size={11} className="text-on-surface-low" /> {baseName(m.file)}<span className="text-on-surface-low">:{m.line}</span></span>
          <span className="w-full truncate font-mono text-on-surface-low text-[0.75rem]">{m.preview}</span>
        </button>
      ))}
    </div>
  )
}

function RailBtn({ icon: Icon, label, active, onClick }: { icon: typeof Search; label: string; active?: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} type="button" title={label} aria-label={label}
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-surface-high"
      style={{ color: active ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}><Icon size={15} /></button>
  )
}

function guessKind(name: string): string {
  const ext = name.toLowerCase().split('.').pop() || ''
  if (ext === 'html' || ext === 'htm') return 'html'
  if (ext === 'svg') return 'svg'
  if (ext === 'json') return 'json'
  if (['md', 'markdown', 'mdx', 'txt'].includes(ext)) return 'markdown'
  return 'text'
}
