import * as React from 'react'
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import { useShellTheme } from '../../shared/shell/shellTheme'
import { GatewayError, gatewayJson, gatewayRequestInit, readGatewayJson } from '../../shared/transport.web'
import type { WorkspaceProject } from './codeRoute'

export type FileEntry = { name: string; path: string; is_dir: boolean; size?: number; mtime?: number }
type FileListing = { roots: FileEntry[]; entries: FileEntry[]; path: string }
export type FileText = { content: string; binary: boolean; truncated: boolean; validator: string }
type Draft = { base: string; text: string; path: string; validator: string }

const row: CSSProperties = { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }

export function withinProject(path: string, root: string): boolean {
  return !!root && (path === root || path.startsWith(root.replace(/\/$/, '') + '/'))
}

function assertProjectPath(path: string, root: string): string {
  if (!withinProject(path, root) || path.split('/').includes('..')) throw new Error('Path is outside the selected project.')
  return path
}

function filePath(path: string): string { return `/api/file-read?path=${encodeURIComponent(path)}` }

export async function listProjectFiles(path: string, root: string): Promise<FileListing> {
  const listed = await gatewayJson<FileListing>(`/api/file-list?path=${encodeURIComponent(assertProjectPath(path, root))}`)
  if (!withinProject(listed.path, root) || listed.entries.some(entry => !withinProject(entry.path, root))) {
    throw new Error('File listing changed outside the selected project.')
  }
  return listed
}

export async function readProjectFile(path: string, root: string): Promise<FileText> {
  const response = await fetch(filePath(assertProjectPath(path, root)), gatewayRequestInit('GET'))
  if (!response.ok) await readGatewayJson(response)
  return { content: await response.text(), binary: response.headers.get('X-Binary') === 'true',
    truncated: response.headers.get('X-Truncated') === 'true', validator: response.headers.get('X-Content-Validator') || '' }
}

export async function saveProjectFile(path: string, root: string, validator: string, text: string): Promise<string> {
  assertProjectPath(path, root)
  if (!validator) throw new Error('This file has no server version. Reload it before saving.')
  const result = await gatewayJson<{ ok: boolean; validator: string }>('/api/file-write', { method: 'POST', body: { path, content: text, expected_validator: validator } })
  if (!result.ok) throw new Error('The server did not save the file.')
  if (!result.validator) throw new Error('The server did not return the saved version.')
  return result.validator
}

export async function renameProjectFile(path: string, root: string, nextName: string): Promise<string> {
  assertProjectPath(path, root)
  if (!nextName || nextName === '.' || nextName === '..' || nextName.includes('/') || nextName.includes('\\')) {
    throw new Error('Enter a single file or folder name.')
  }
  const parent = path.slice(0, path.lastIndexOf('/'))
  const dest = assertProjectPath(`${parent}/${nextName}`, root)
  const moved = await gatewayJson<{ ok: boolean; path: string }>('/api/file-move', { method: 'POST', body: { src: path, dest } })
  if (!moved.ok || moved.path !== dest) throw new Error('The server did not confirm the rename.')
  return dest
}

function errorText(error: unknown): string {
  if (error instanceof GatewayError) {
    if (error.status === 404) return 'This file or folder is missing. Refresh the project and choose another file.'
    if (error.status === 403 || error.status === 400) return 'Access to this path was denied. Choose a file inside the selected project.'
    if (error.status === 409) return 'The destination already exists. Choose another name.'
  }
  return error instanceof Error ? error.message : 'The file operation failed.'
}

function draftKey(scope: OwnerScope, projectId: string, path: string): string {
  return `gideon:code-draft:${encodeURIComponent(scope.cacheKey)}:${encodeURIComponent(projectId)}:${encodeURIComponent(path)}`
}

function readDraft(key: string): Draft | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (value && typeof value === 'object' && 'base' in value && 'text' in value && 'path' in value && 'validator' in value &&
      typeof value.base === 'string' && typeof value.text === 'string' && typeof value.path === 'string' && typeof value.validator === 'string') return value as Draft
  } catch { }
  return null
}

function storeDraft(key: string, draft: Draft | null): void {
  try { if (draft) sessionStorage.setItem(key, JSON.stringify(draft)); else sessionStorage.removeItem(key) }
  catch { }
}

export function moveProjectDraft(scope: OwnerScope, projectId: string, from: string, to: string): void {
  const oldKey = draftKey(scope, projectId, from)
  const stored = readDraft(oldKey)
  if (!stored) return
  storeDraft(draftKey(scope, projectId, to), { ...stored, path: to })
  storeDraft(oldKey, null)
}

export type CodeFilesProps = { project: WorkspaceProject; scope: OwnerScope }

export default function CodeFiles({ project, scope }: CodeFilesProps) {
  return <CodeFilesProject key={`${scope.cacheKey}:${project.project.id}:${project.project.workspace_dir}`} project={project} scope={scope} />
}

function CodeFilesProject({ project, scope }: CodeFilesProps) {
  const { palette } = useShellTheme()
  const card: CSSProperties = { background: palette.card, border: `1px solid ${palette.line}`, borderRadius: 14, padding: 16, minWidth: 0 }
  const control: CSSProperties = { minHeight: 40, padding: '8px 12px', borderRadius: 8, cursor: 'pointer',
    color: palette.text, background: palette.secondary, border: `1px solid ${palette.line}` }
  const root = project.project.workspace_dir.replace(/\/$/, '')
  const [directory, setDirectory] = useState(root)
  const [entries, setEntries] = useState<FileEntry[]>([])
  const [selected, setSelected] = useState<FileEntry | null>(null)
  const [base, setBase] = useState('')
  const [validator, setValidator] = useState('')
  const [draft, setDraft] = useState('')
  const [binary, setBinary] = useState(false)
  const [truncated, setTruncated] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [serverVersion, setServerVersion] = useState('')
  const [missing, setMissing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [rename, setRename] = useState<FileEntry | null>(null)
  const [nextName, setNextName] = useState('')
  const [revision, setRevision] = useState(0)
  const editor = useRef<HTMLTextAreaElement>(null)
  const generation = useRef(0)
  const live = useRef(true)
  useEffect(() => {
    live.current = true
    return () => { live.current = false; generation.current++ }
  }, [])
  const current = useCallback((request: number) => live.current && generation.current === request, [])
  const dirty = !!selected && !binary && draft !== base
  const selectedPath = selected?.path ?? ''
  const key = selected ? draftKey(scope, project.project.id, selected.path) : ''

  const loadDirectory = useCallback(async (path: string) => {
    const request = ++generation.current
    setBusy(true); setError('')
    try {
      const result = await listProjectFiles(path, root)
      if (current(request)) { setDirectory(result.path); setEntries(result.entries) }
    } catch (failure) { if (current(request)) setError(errorText(failure)) }
    finally { if (current(request)) setBusy(false) }
  }, [root, current])

  useEffect(() => {
    generation.current++
    setDirectory(root); setEntries([]); setSelected(null); setDraft(''); setBase(''); setValidator(''); setError(''); setNotice('')
    if (root) void loadDirectory(root)
  }, [root, project.project.id, scope.cacheKey, loadDirectory])

  const open = useCallback(async (entry: FileEntry) => {
    if (entry.is_dir) { await loadDirectory(entry.path); return }
    const current = ++generation.current
    setError(''); setNotice(''); setConflict(false); setServerVersion(''); setMissing(false); setBusy(true)
    try {
      const result = await readProjectFile(entry.path, root)
      if (!live.current || generation.current !== current) return
      const stored = readDraft(draftKey(scope, project.project.id, entry.path))
      setSelected(entry); setBase(result.content); setDraft(stored?.text ?? result.content); setValidator(stored?.validator ?? result.validator)
      setBinary(result.binary); setTruncated(result.truncated)
      setConflict(!!stored && stored.validator !== result.validator)
      if (stored && stored.validator !== result.validator) setServerVersion(result.content)
      if (stored) setNotice('Recovered an unsaved draft from this browser session.')
    } catch (failure) {
      if (!live.current || generation.current !== current) return
      setSelected(entry); setMissing(true); setError(errorText(failure))
      const stored = readDraft(draftKey(scope, project.project.id, entry.path))
      setDraft(stored?.text ?? ''); setBase(stored?.base ?? ''); setValidator(stored?.validator ?? '')
      if (stored) setNotice('Your unsaved draft is still available below.')
    } finally { if (live.current && generation.current === current) setBusy(false) }
  }, [root, scope.cacheKey, project.project.id, loadDirectory])

  async function reload() {
    if (!selected) return
    const request = ++generation.current
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await readProjectFile(selected.path, root)
      if (!current(request)) return
      setBinary(result.binary); setTruncated(result.truncated); setMissing(false)
      if (draft !== base && result.validator !== validator) { setConflict(true); setServerVersion(result.content) }
      else if (draft === base) { setDraft(result.content); setConflict(false) }
      setBase(result.content); setValidator(result.validator)
      setNotice('File reloaded from the server.')
    } catch (failure) { if (current(request)) { setMissing(true); setError(errorText(failure)) } }
    finally { if (current(request)) setBusy(false) }
  }

  async function save(overwrite = false) {
    if (!selected || busy || binary || truncated || missing) return
    const request = ++generation.current
    setBusy(true); setError(''); setNotice('')
    try {
      const expected = overwrite ? (await readProjectFile(selected.path, root)).validator : validator
      if (!current(request)) return
      const savedValidator = await saveProjectFile(selected.path, root, expected, draft)
      if (!current(request)) return
      setBase(draft); setValidator(savedValidator); setConflict(false); setServerVersion(''); storeDraft(key, null); setNotice('Saved to the selected project.')
      setRevision(value => value + 1)
    } catch (failure) {
      if (!current(request)) return
      if (failure instanceof GatewayError && failure.status === 409) {
        setConflict(true); setError('The file changed on the server. Reload it to compare, or confirm overwrite. Your draft is preserved.')
        try { const latest = await readProjectFile(selected.path, root); if (current(request)) setServerVersion(latest.content) } catch { }
      } else { setError(errorText(failure)); if (failure instanceof GatewayError && failure.status === 404) setMissing(true) }
    }
    finally { if (current(request)) setBusy(false) }
  }

  async function doRename(entry: FileEntry) {
    const request = ++generation.current
    setBusy(true); setError(''); setNotice('')
    try {
      const dest = await renameProjectFile(entry.path, root, nextName.trim())
      const moved = (path: string) => path === entry.path ? dest : path.startsWith(`${entry.path}/`) ? dest + path.slice(entry.path.length) : path
      if (!current(request)) return
      if (selected && moved(selected.path) !== selected.path) moveProjectDraft(scope, project.project.id, selected.path, moved(selected.path))
      if (selected && moved(selected.path) !== selected.path) {
        setSelected({ ...selected, path: moved(selected.path), name: moved(selected.path).split('/').pop() || selected.name })
      }
      setDirectory(path => moved(path)); setRename(null); setNextName(''); setRevision(value => value + 1)
      setNotice('Renamed in the selected project.')
    } catch (failure) { if (current(request)) setError(errorText(failure)) }
    finally { if (current(request)) setBusy(false) }
  }

  useEffect(() => { if (revision) void loadDirectory(directory) }, [revision])
  useEffect(() => {
    if (!selected || binary) return
    storeDraft(key, draft === base ? null : { path: selected.path, base, text: draft, validator })
  }, [key, selectedPath, draft, base, binary, validator])
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's' && selected) {
        event.preventDefault(); void save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const parent = directory === root ? '' : directory.slice(0, directory.lastIndexOf('/'))
  return <section aria-label="Project files" style={{ display: 'grid', gap: 12, color: palette.text }}>
    <header style={row}><h2 style={{ margin: 0, flex: 1 }}>Files</h2>
      <button type="button" style={control} disabled={busy || !root} onClick={() => void loadDirectory(directory)}>Refresh files</button></header>
    <p style={{ margin: 0, overflowWrap: 'anywhere' }}>Project: {project.project.name} · {root || 'No workspace path'}</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!root && <p>This project has no workspace path. Choose a project with an accessible workspace.</p>}
    {root && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 310px), 1fr))', gap: 12 }}>
      <nav aria-label="Project file browser" style={card}>
        <div style={row}><strong style={{ overflowWrap: 'anywhere', flex: 1 }}>{directory}</strong>
          {parent && <button type="button" style={control} onClick={() => void loadDirectory(parent)}>Up one folder</button>}</div>
        {entries.length === 0 && !busy && <p>No files in this folder.</p>}
        <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0', display: 'grid', gap: 4 }}>
          {entries.map(entry => <li key={entry.path} style={{ ...row, flexWrap: 'nowrap' }}>
            <button type="button" style={{ ...control, flex: 1, minWidth: 0, textAlign: 'left', overflowWrap: 'anywhere' }}
              aria-current={selected?.path === entry.path ? 'page' : undefined} aria-label={`${entry.is_dir ? 'Open folder' : 'Open file'} ${entry.name}`}
              onClick={() => void open(entry)}>{entry.is_dir ? '▸ ' : ''}{entry.name}</button>
            <button type="button" style={control} aria-label={`Rename ${entry.name}`} onClick={() => { setRename(entry); setNextName(entry.name) }}>Rename</button>
          </li>)}
        </ul>
        {rename && <form aria-label="Rename file or folder" onSubmit={event => { event.preventDefault(); void doRename(rename) }} style={{ display: 'grid', gap: 8 }}>
          <label htmlFor="code-rename">New name for {rename.name}</label>
          <input id="code-rename" autoFocus value={nextName} onChange={event => setNextName(event.target.value)} style={{ ...control, background: palette.canvas }} />
          <div style={row}><button type="submit" style={control} disabled={busy}>Rename</button><button type="button" style={control} onClick={() => setRename(null)}>Cancel</button></div>
        </form>}
      </nav>
      <div style={card}>
        {!selected && <p>Choose a file to review or edit.</p>}
        {selected && <div style={{ display: 'grid', gap: 10 }}>
          <div style={row}><h3 style={{ margin: 0, overflowWrap: 'anywhere', flex: 1 }}>{selected.name}</h3>
            <button type="button" style={control} disabled={busy} onClick={() => void reload()}>Reload file</button></div>
          <p style={{ margin: 0, overflowWrap: 'anywhere' }}>{selected.path}</p>
          {binary && <p role="status">Binary file. Text editing is unavailable.</p>}
          {truncated && <p role="status">This file exceeds the text preview limit. Editing is unavailable.</p>}
          {missing && <p role="status">The file is unavailable. Your draft remains below for recovery.</p>}
          {conflict && <p role="alert">The server file changed since this draft was opened. Review the server version before overwriting.</p>}
          {conflict && serverVersion && <details><summary>Show current server version</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{serverVersion}</pre></details>}
          {!binary && <><label htmlFor="code-file-editor">File editor for {selected.name}</label>
            <textarea id="code-file-editor" ref={editor} value={draft} onChange={event => setDraft(event.target.value)}
              readOnly={truncated || missing} spellCheck={false} rows={18}
              style={{ width: '100%', minHeight: 260, boxSizing: 'border-box', resize: 'vertical', fontFamily: 'monospace', fontSize: 14,
                color: palette.text, background: palette.canvas, border: `1px solid ${palette.line}` }} />
            <div style={row}><button type="button" style={control} disabled={busy || truncated || missing || !dirty || conflict}
              onClick={() => void save()}>Save file</button>
              {conflict && <button type="button" style={control} disabled={busy || missing}
                onClick={() => { if (window.confirm('Overwrite the newer server file with this draft?')) void save(true) }}>Overwrite server file</button>}
              <button type="button" style={control} disabled={!dirty} onClick={() => editor.current?.focus()}>Focus draft</button>
              {dirty && <span>Unsaved changes</span>}
            </div></>}
        </div>}
      </div>
    </div>}
  </section>
}
