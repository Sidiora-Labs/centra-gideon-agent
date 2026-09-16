import { useCallback, useEffect, useMemo, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { Check, CornerDownLeft, CornerLeftUp, Folder, FolderPlus, GitBranch, Loader2 } from 'lucide-react'
import { Modal } from '../../shared/ui/Modal'
import { SearchField } from '../../shared/ui/SearchField'
import { Button } from '../../shared/ui/Button'
import { unavailableWhen, BUSY_REASON } from '../../shared/ui/unavailable'
import { api } from '../../shared/data/api'

export function WorkspacePicker({ mode, allowCreate, onPick, onClose }: {
  mode: 'brownfield' | 'greenfield'
  allowCreate?: boolean
  onPick: (absPath: string) => void
  onClose: () => void
}) {
  const canCreate = mode === 'greenfield' || !!allowCreate
  const [path, setPath] = useState('')
  const [parent, setParent] = useState('')
  const [dirs, setDirs] = useState<{ name: string; path: string; is_repo?: boolean }[]>([])
  const [inRepo, setInRepo] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [pathDraft, setPathDraft] = useState('')
  const [filter, setFilter] = useState('')

  const browse = useCallback(async (to?: string, from?: string) => {
    setLoading(true); setError(null)
    try {
      const r = await api.browseDirs(to)
      setPath(r.path); setParent(r.parent); setDirs(r.dirs); setPathDraft(r.path); setFilter(''); setInRepo(!!r.in_repo)
    } catch (e) {
      setError((e as Error).message || 'Could not open that directory')
      if (from !== undefined) setPathDraft(from)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void browse() }, [browse])

  const shownDirs = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return needle ? dirs.filter((d) => d.name.toLowerCase().includes(needle)) : dirs
  }, [dirs, filter])

  async function createFolder() {
    if (submitting) return
    const name = newName.trim()
    if (!name) return
    if (/[/\\]/.test(name) || name === '.' || name === '..') {
      setError('Folder name can’t contain slashes — it’s created here. Navigate into a folder first if you want it nested.')
      return
    }
    const target = `${path.replace(/\/$/, '')}/${name}`
    setSubmitting(true)
    try {
      const r = await api.createDir(target)
      setCreating(false); setNewName(''); setError(null)
      onPick(r?.path || target)
    } catch (e) {
      setError((e as Error).message || 'Could not create the folder')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={mode === 'brownfield' ? 'Choose the codebase directory' : 'Choose where to create the project'}
      icon={<Folder size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex min-h-[360px] flex-col gap-3">
        { }
        <div className="flex items-center gap-2">
          {
}
          <button type="button" onClick={() => parent && parent !== path && browse(parent)}
            aria-label="Up one level"
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface aria-disabled:opacity-40 aria-disabled:hover:bg-transparent aria-disabled:hover:text-on-surface-low aria-disabled:cursor-default"
            {...unavailableWhen(!parent || parent === path, 'Already at the top level', { title: 'Up one level' })}>
            <CornerLeftUp size={16} />
          </button>
          {
}
          <input value={pathDraft} onChange={(e) => setPathDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { const t = pathDraft.trim(); if (t && t !== path) void browse(t, path) }
              else if (e.key === 'Escape') setPathDraft(path)
            }}
            onBlur={() => setPathDraft(path)}
            spellCheck={false} autoCapitalize="off" autoCorrect="off" aria-label="Workspace path"
            placeholder="/absolute/path/to/folder"
            data-type="body-s" className="min-w-0 flex-1 rounded-md bg-surface-high px-2.5 py-1.5 font-mono text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        </div>

        { }
        {!loading && dirs.length > 8 && (
          <div className="flex items-center gap-2 rounded-md bg-surface-high px-2.5 py-1.5">
            <SearchField variant="inline" size="md" value={filter} onChange={setFilter} ariaLabel="Filter folders"
              placeholder={`Filter ${dirs.length} folders…`} spellCheck={false} autoCapitalize="off" autoCorrect="off"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && shownDirs.length > 0) { e.preventDefault(); void browse(shownDirs[0].path) }
                else if (e.key === 'Escape' && filter) { e.preventDefault(); setFilter('') }
              }} />
            {
}
            <ResultAnnouncement count={shownDirs.length} noun="folders" active={!!filter.trim()} />
            {
}
            {shownDirs.length > 0 && (
              <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 pl-1 text-on-surface-low">
                <CornerDownLeft size={11} /> opens the first folder
              </span>
            )}
          </div>
        )}

        { }
        <div className="min-h-0 flex-1 overflow-y-auto rounded-lg border border-outline-variant/40">
          {loading ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
          ) : dirs.length === 0 ? (
            <div data-type="body-s" className="flex h-40 items-center justify-center px-4 text-center text-on-surface-low">No sub-folders here.</div>
          ) : shownDirs.length === 0 ? (
            <div data-type="body-s" className="flex h-40 items-center justify-center px-4 text-center text-on-surface-low">No folders match “{filter.trim()}”.</div>
          ) : (
            <div className="flex flex-col">
              {shownDirs.map((d) => (
                <div key={d.path} data-type="body-s" className="group/row flex items-center gap-2 px-3 py-2 text-on-surface-var transition-colors hover:bg-surface-high">
                  <button type="button" onClick={() => browse(d.path)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left hover:text-on-surface" title={`Open ${d.name}`}>
                    <Folder size={15} className="shrink-0 text-on-surface-low" />
                    <span className="truncate">{d.name}</span>
                    {d.is_repo && (
                      <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 fw-500"
                        style={{ color: 'var(--color-primary)', border: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' }}
                        title="This folder is a git repository">
                        <GitBranch size={10} /> repo
                      </span>
                    )}
                  </button>
                  {
}
                  <button type="button" onClick={() => onPick(d.path)}
                    data-type="caption" className="shrink-0 rounded px-1.5 py-0.5 text-on-surface-low opacity-50 transition-opacity hover:bg-surface-highest hover:text-primary hover:opacity-100 focus-visible:opacity-100 group-hover/row:opacity-100"
                    title={mode === 'brownfield' ? `Use ${d.name} as the codebase` : `Use ${d.name} as the project home`}>
                    Use
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div role="alert" data-type="body-s" className="rounded-lg px-3 py-2"
            style={{ background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)', color: 'var(--color-danger)' }}>{error}</div>
        )}

        { }
        {canCreate && (
          creating ? (
            <div className="flex items-center gap-2">
              <input autoFocus value={newName} onChange={(e) => { setNewName(e.target.value); if (error) setError(null) }}
                onKeyDown={(e) => { if (e.key === 'Enter') createFolder(); else if (e.key === 'Escape' && !submitting) { setCreating(false); setError(null) } }}
                disabled={submitting} placeholder="new-project-folder"
                data-type="body-s" className="h-9 min-w-0 flex-1 rounded-md bg-surface-high px-2.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary disabled:opacity-60" />
              <Button size="sm" onClick={createFolder} loading={submitting} disabled={!newName.trim() || submitting}
                disabledReason={!newName.trim() ? 'Enter a folder name first' : undefined}><Check size={14} /> Create + use
              </Button>
            </div>
          ) : (
            <button type="button" onClick={() => setCreating(true)}
              data-type="body-s" className="inline-flex items-center gap-1.5 self-start rounded-md px-2 py-1.5 text-on-surface-low hover:text-on-surface">
              <FolderPlus size={15} /> New folder here
            </button>
          )
        )}

        {
}
        <div className="flex items-center justify-between gap-2 border-t border-outline-variant/40 pt-3">
          <span data-type="caption" className="min-w-0 text-on-surface-low">
            {mode === 'brownfield' ? 'Open a folder to navigate; use the current one as the codebase.' : 'Create a new folder, or use the current one as the project home.'}
            {
}
            {mode === 'brownfield' && path && !loading && (
              inRepo
                ? <span className="ml-1.5 inline-flex items-center gap-1" style={{ color: 'var(--color-ok)' }}><GitBranch size={11} /> git repo</span>
                : <span className="ml-1.5 text-on-surface-low/70">· not a git repo (changes won’t be version-tracked)</span>
            )}
          </span>
          <Button size="sm" onClick={() => path && onPick(path)} disabled={!path || loading} disabledReason={!path && !loading ? 'Choose a folder first' : BUSY_REASON}>
            <Check size={14} /> Use this folder
          </Button>
        </div>
      </div>
    </Modal>
  )
}
