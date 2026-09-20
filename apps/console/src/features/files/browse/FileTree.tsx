import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronRight, ChevronDown, Pencil, Trash2, Upload, FilePlus2, FolderPlus, MoreHorizontal } from 'lucide-react'
import { createPortal } from 'react-dom'
import type { FsEntry } from '../../../shared/data/api'
import { menuCursorKeydown, useMenuCursor } from '../../../shared/data/useMenuCursor'
import { fileIcon, gitBadge, gitStatusTitle } from '../fileMeta'
import type { useDirCache } from '../filesData'

interface TreeProps {
  dirs: ReturnType<typeof useDirCache>
  rootPath: string
  activePath: string | null
  gitStatuses: Record<string, string>
  onOpenFile: (entry: FsEntry) => void
  artifactPaths: Set<string>
  onRename: (entry: FsEntry, nextName: string) => void
  onDelete: (entry: FsEntry) => void
  onUpload: (dirEntry: FsEntry, files: File[]) => void
  onCreate?: (dirEntry: FsEntry, name: string, kind: 'file' | 'dir') => void
  hideNames?: Set<string>
  hidePrefixes?: Set<string>
  hideNamesDeep?: Set<string>
  emptyLabel?: string
}

export function FileTree({ dirs, rootPath, activePath, gitStatuses, onOpenFile, artifactPaths, onRename, onDelete, onUpload, onCreate, hideNames, hidePrefixes, hideNamesDeep, emptyLabel = 'Empty' }: TreeProps) {
  const [entries, setEntries] = useState<FsEntry[] | null>(() => dirs.cache[rootPath] ?? null)
  const rootCached = dirs.cache[rootPath]
  useEffect(() => {
    let alive = true
    if (rootCached) setEntries(rootCached)
    dirs.load(rootPath, !dirs.resolved[rootPath]).then((e) => { if (alive) setEntries(e) })
    return () => { alive = false }
  }, [rootPath, dirs, rootCached])

  if (entries === null) return <FileTreeSkeleton />
  if (dirs.errors[rootPath]) return <div role="alert" className="px-m py-s text-danger text-[0.8125rem]">Couldn&rsquo;t open this path: {dirs.errors[rootPath]}</div>
  let shown = hideNames?.size ? entries.filter((e) => !hideNames.has(e.name)) : entries
  if (hidePrefixes?.size) shown = shown.filter((e) => ![...hidePrefixes].some((p) => e.name.startsWith(p)))
  if (hideNamesDeep?.size) shown = shown.filter((e) => !hideNamesDeep.has(e.name))
  if (shown.length === 0) return <div className="px-m py-s text-on-surface-low text-[0.8125rem]">{emptyLabel}</div>
  return (
    <div>
      {shown.map((e) => (
        <TreeNode key={e.path} entry={e} depth={0} dirs={dirs} activePath={activePath}
          gitStatuses={gitStatuses} onOpenFile={onOpenFile} artifactPaths={artifactPaths}
          onRename={onRename} onDelete={onDelete} onUpload={onUpload} onCreate={onCreate} hideNamesDeep={hideNamesDeep} />
      ))}
    </div>
  )
}

function TreeNode({ entry, depth, dirs, activePath, gitStatuses, onOpenFile, artifactPaths, onRename, onDelete, onUpload, onCreate, hideNamesDeep }: {
  entry: FsEntry; depth: number
} & Omit<TreeProps, 'rootPath'>) {
  const [open, setOpen] = useState(false)
  const [children, setChildren] = useState<FsEntry[] | null>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [renaming, setRenaming] = useState(false)
  const committedRename = useRef(false)
  const cancelledRename = useRef(false)
  const [draft, setDraft] = useState(entry.name)
  const [dropActive, setDropActive] = useState(false)
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [createDraft, setCreateDraft] = useState('')
  const committedCreate = useRef(false)
  const cancelledCreate = useRef(false)
  const uploadInput = useRef<HTMLInputElement>(null)
  const Icon = fileIcon(entry.name, entry.is_dir)
  const badge = gitBadge(gitStatuses[entry.path])
  const isActive = activePath === entry.path

  useEffect(() => {
    if (!entry.is_dir || !activePath) return
    const norm = (p: string) => p.replace(/^\/private(\/|$)/, '/')
    if (!norm(activePath).startsWith(norm(entry.path).replace(/\/$/, '') + '/')) return
    setOpen(true)
    if (children === null) dirs.load(entry.path).then(setChildren)
  }, [activePath, entry.is_dir, entry.path, children, dirs])

  const dirCached = entry.is_dir ? dirs.cache[entry.path] : undefined
  useEffect(() => {
    if (!entry.is_dir || !open) return
    let alive = true
    dirs.load(entry.path).then((c) => { if (alive) setChildren(c) })
    return () => { alive = false }
  }, [entry.is_dir, entry.path, open, dirs, dirCached])

  const toggle = async () => {
    if (entry.is_dir) {
      const next = !open
      setOpen(next)
      if (next && children === null) setChildren(await dirs.load(entry.path))
    } else {
      onOpenFile(entry)
    }
  }

  const startRename = () => { committedRename.current = false; cancelledRename.current = false; setDraft(entry.name); setRenaming(true) }
  const commitRename = () => {
    if (cancelledRename.current) return

    if (committedRename.current) return
    committedRename.current = true
    const next = draft.trim()
    setRenaming(false)
    if (next && next !== entry.name) onRename(entry, next)
  }

  const startCreate = async (kind: 'file' | 'dir') => {
    if (!entry.is_dir) return
    if (!open) { setOpen(true); if (children === null) setChildren(await dirs.load(entry.path)) }
    committedCreate.current = false; cancelledCreate.current = false
    setCreateDraft(''); setCreating(kind)
  }
  const commitCreate = () => {
    if (committedCreate.current || cancelledCreate.current) return
    committedCreate.current = true
    const name = createDraft.trim()
    const kind = creating
    setCreating(null); setCreateDraft('')
    if (name && kind && onCreate) onCreate(entry, name, kind)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation()
    setDropActive(false)
    if (!entry.is_dir) return
    const files = Array.from(e.dataTransfer.files)
    if (files.length) onUpload(entry, files)
  }

  const openMenuFromButton = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setMenu({ x: Math.round(r.right), y: Math.round(r.bottom) })
  }

  return (
    <div>
      <div className="group/row relative"
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }) }}
        onDragOver={entry.is_dir ? (e) => { e.preventDefault(); e.stopPropagation(); setDropActive(true) } : undefined}
        onDragLeave={entry.is_dir ? () => setDropActive(false) : undefined}
        onDrop={entry.is_dir ? onDrop : undefined}
      >
        {renaming ? (
          <div className="flex items-center gap-1.5 py-1.5 pr-2" style={{ paddingLeft: 10 + depth * 16 }}>
            <span className="w-[15px] shrink-0" />
            <Icon size={16} className="shrink-0" style={{ color: entry.is_dir ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            <input autoFocus aria-label="Rename this file or folder" value={draft} onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') { cancelledRename.current = true; setRenaming(false) } }}
              onBlur={commitRename}
              className="h-6 min-w-0 flex-1 rounded-md bg-surface-high px-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          </div>
        ) : (
          <button onClick={toggle} type="button"
            aria-expanded={entry.is_dir ? open : undefined}
            aria-current={isActive ? 'page' : undefined}
            className="group flex w-full items-center gap-1.5 rounded-md py-1.5 pr-2 text-left transition-colors hover:bg-surface-high"
            style={{
              paddingLeft: 10 + depth * 16,
              background: dropActive ? 'color-mix(in srgb, var(--color-primary) 18%, transparent)'
                : isActive ? 'color-mix(in srgb, var(--color-primary) 14%, transparent)' : undefined,
              boxShadow: dropActive ? 'inset 0 0 0 1px var(--color-primary)' : undefined,
            }}>
            {entry.is_dir
              ? (open ? <ChevronDown size={15} className="shrink-0 text-on-surface-low" /> : <ChevronRight size={15} className="shrink-0 text-on-surface-low" />)
              : <span className="w-[15px] shrink-0" />}
            <Icon size={16} className="shrink-0" style={{ color: entry.is_dir ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
            <span className="truncate text-[0.8125rem]" style={{ color: isActive ? 'var(--color-on-surface)' : undefined }}>{entry.name}</span>
            {!entry.is_dir && artifactPaths.has(entry.path) && (
              <span className="ml-1 size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Saved as an artifact" />
            )}
            {badge && (
              <span className="ml-auto shrink-0 rounded px-1 text-[0.75rem] font-semibold leading-tight transition-[margin] group-hover/row:mr-5 group-focus-within/row:mr-5"
                style={{ color: badge.tone, border: `1px solid color-mix(in srgb, ${badge.tone} 40%, transparent)` }} title={gitStatusTitle(gitStatuses[entry.path])}>
                {badge.label}
              </span>
            )}
          </button>
        )}
        {
}
        {!renaming && (
          <button type="button" onClick={openMenuFromButton} aria-label={`Actions for ${entry.name}`}
            className="absolute right-0.5 top-1/2 -translate-y-1/2 grid size-6 place-items-center rounded text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-on-surface focus-visible:opacity-100 group-hover/row:opacity-100">
            <MoreHorizontal size={13} />
          </button>
        )}
      </div>
      {entry.is_dir && (
        <input ref={uploadInput} type="file" multiple className="hidden"
          name={`upload-${entry.path}`} aria-label={`Upload files to ${entry.name}`} tabIndex={-1}
          onChange={(e) => { const fs = Array.from(e.target.files ?? []); if (fs.length) onUpload(entry, fs); e.target.value = '' }} />
      )}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}
          items={[
            ...(entry.is_dir && onCreate ? [
              { icon: FilePlus2, label: 'New file here', onClick: () => { void startCreate('file') } },
              { icon: FolderPlus, label: 'New folder here', onClick: () => { void startCreate('dir') } },
            ] : []),
            { icon: Pencil, label: 'Rename', onClick: startRename },
            ...(entry.is_dir ? [{ icon: Upload, label: 'Upload here', onClick: () => uploadInput.current?.click() }] : []),
            { icon: Trash2, label: 'Delete', tone: 'danger' as const, onClick: () => onDelete(entry) },
          ]} />
      )}
      {entry.is_dir && open && children !== null && (() => {
        const kids = hideNamesDeep?.size ? children.filter((c) => !hideNamesDeep.has(c.name)) : children
        return (
        <div>
          { }
          {creating && (
            <div className="flex items-center gap-1.5 py-1.5 pr-2" style={{ paddingLeft: 10 + (depth + 1) * 16 }}>
              <span className="w-[15px] shrink-0" />
              {creating === 'dir'
                ? <FolderPlus size={16} className="shrink-0 text-primary" />
                : <FilePlus2 size={16} className="shrink-0 text-on-surface-low" />}
              <input autoFocus value={createDraft} onChange={(e) => setCreateDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitCreate(); if (e.key === 'Escape') { cancelledCreate.current = true; setCreating(null); setCreateDraft('') } }}
                onBlur={commitCreate} placeholder={creating === 'file' ? 'new-file.ext' : 'new-folder'}
                className="h-6 min-w-0 flex-1 rounded-md bg-surface-high px-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary placeholder:text-on-surface-low" />
            </div>
          )}
          {kids.length === 0 && !creating
            ? <div className="py-1 text-on-surface-low text-[0.8125rem]" style={{ paddingLeft: 10 + (depth + 1) * 16 + 15 }}>empty</div>
            : kids.map((c) => (
              <TreeNode key={c.path} entry={c} depth={depth + 1} dirs={dirs} activePath={activePath}
                gitStatuses={gitStatuses} onOpenFile={onOpenFile} artifactPaths={artifactPaths}
                onRename={onRename} onDelete={onDelete} onUpload={onUpload} onCreate={onCreate} hideNamesDeep={hideNamesDeep} />
            ))}
        </div>
        )
      })()}
    </div>
  )
}

function FileTreeSkeleton() {
  const widths = ['62%', '78%', '45%', '70%', '55%', '83%', '50%']
  return (
    <div className="px-m py-s flex flex-col gap-2 animate-pulse" aria-hidden="true">
      {widths.map((w, i) => (
        <div key={i} className="flex items-center gap-1.5" style={{ paddingLeft: (i % 3) * 14 }}>
          <span className="size-3.5 shrink-0 rounded-sm bg-surface-high" />
          <span className="h-3 rounded bg-surface-high" style={{ width: w }} />
        </div>
      ))}
    </div>
  )
}

interface MenuItem { icon: typeof Pencil; label: string; onClick: () => void; tone?: 'danger' }

function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const { move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: ref, count: items.length, openKey: `${x},${y}`,
  })
  const closeAndReturnFocus = useCallback(() => { onClose(); restoreFocus() }, [onClose, restoreFocus])
  const estW = 200, estH = Math.min(items.length * 40 + 16, 360)
  const [pos, setPos] = useState(() => ({
    left: Math.min(x, Math.max(8, window.innerWidth - estW - 8)),
    top: Math.min(y, Math.max(8, window.innerHeight - estH - 8)),
  }))
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setPos({
      left: Math.min(x, Math.max(8, window.innerWidth - r.width - 8)),
      top: Math.min(y, Math.max(8, window.innerHeight - r.height - 8)),
    })
  }, [x, y, items.length])
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); closeAndReturnFocus(); return }
      menuCursorKeydown(e, { move, dismiss: closeAndReturnFocus })
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onClose, true)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); window.removeEventListener('scroll', onClose, true) }
  }, [onClose, closeAndReturnFocus, move])

  return createPortal(
    <div ref={ref} role="menu" aria-orientation="vertical"
      className="fixed z-[var(--z-menu)] min-w-[160px] rounded-lgi bg-surface-container p-s"
      style={{ left: pos.left, top: pos.top, boxShadow: 'var(--shadow-menu)' }}>
      {items.map((it, i) => (
        <button key={it.label} type="button" role="menuitem" tabIndex={tabIndexFor(i)}
          onClick={() => { it.onClick(); closeAndReturnFocus() }}
          className="flex w-full items-center gap-s rounded-md px-m py-s text-left text-[0.8125rem] transition-colors hover:bg-surface-high"
          style={{ color: it.tone === 'danger' ? 'var(--color-danger)' : 'var(--color-on-surface)' }}>
          <it.icon size={15} className="shrink-0" />
          <span className="truncate">{it.label}</span>
        </button>
      ))}
    </div>,
    document.body,
  )
}
