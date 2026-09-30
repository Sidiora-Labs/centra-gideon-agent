import { useMemo, useState } from 'react'
import { Download, FolderOpen, ShieldCheck } from 'lucide-react'
import { api, type AgentExportPreview, type SavedAgent } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { Modal } from '../../shared/ui/Modal'
import { isReservedAgent } from './agentMeta'

export function ExportAgentsDialog({ agents, defaultAgent, initialNames, onClose }: {
  agents: SavedAgent[]
  defaultAgent: string
  initialNames?: string[]
  onClose: () => void
}) {
  const eligible = useMemo(() => agents.filter(agent =>
    !isReservedAgent(agent) && agent.name !== defaultAgent && ['local', 'gideon'].includes(agent.source ?? ''),
  ), [agents, defaultAgent])
  const [selected, setSelected] = useState<string[]>(() => {
    const initial = initialNames?.filter(name => eligible.some(agent => agent.name === name))
    return initial?.length ? initial : eligible.map(agent => agent.name)
  })
  const [destination, setDestination] = useState('')
  const [preview, setPreview] = useState<AgentExportPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [written, setWritten] = useState<string[] | null>(null)

  const changeSelection = (name: string, checked: boolean) => {
    setPreview(null)
    setError('')
    setSelected(current => checked ? [...current, name] : current.filter(item => item !== name))
  }
  const makePreview = async () => {
    setBusy(true)
    setError('')
    setPreview(null)
    try {
      const result = await api.previewAgentExport({
        agents: selected,
        ...(destination.trim() ? { destination: destination.trim() } : {}),
      })
      setPreview(result)
      if (!result.preview_token) setError('Review the destination conflicts or blocked content before exporting.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Preview failed. No files were written.')
    } finally {
      setBusy(false)
    }
  }
  const writeExport = async () => {
    if (!preview?.preview_token) return
    setBusy(true)
    setError('')
    try {
      const result = await api.writeAgentExport(preview.preview_token)
      setWritten(result.files)
      setPreview(null)
    } catch (cause) {
      setPreview(null)
      setError(cause instanceof Error ? cause.message : 'Export failed. Preview again before retrying.')
    } finally {
      setBusy(false)
    }
  }

  return <Modal title="Export agents to Claude Code" icon={<Download size={19} />} onClose={onClose}>
    <div className="grid gap-l">
      <p data-type="body-s" className="text-on-surface-low">Create local Claude Code agent files from your owner-created Gideon agents. The default and built-in agents are excluded. Gideon settings are never changed.</p>
      {eligible.length === 0 ? <p data-type="body-s" className="rounded-lg border border-outline-variant/30 bg-surface-container p-m text-on-surface-low">There are no exportable owner-created agents.</p> : <fieldset className="grid gap-s">
        <legend data-type="label-m" className="mb-s text-on-surface">Choose agents</legend>
        {eligible.map(agent => <label key={agent.name} className="flex min-h-10 items-center gap-m rounded-lg border border-outline-variant/25 bg-surface-container/45 px-m py-s text-on-surface">
          <input type="checkbox" checked={selected.includes(agent.name)} onChange={event => changeSelection(agent.name, event.target.checked)} />
          <span className="min-w-0 flex-1"><span data-type="label-s">{agent.name}</span>{agent.description && <span data-type="caption" className="ml-m text-on-surface-low">{agent.description}</span>}</span>
        </label>)}
      </fieldset>}
      <label className="grid gap-s text-on-surface" data-type="label-s">
        Destination directory
        <span className="flex items-center gap-s rounded-lg border border-outline-variant/40 bg-surface-container px-m focus-within:border-primary">
          <FolderOpen size={16} className="shrink-0 text-on-surface-low" />
          <input aria-label="Destination directory" value={destination} onChange={event => { setDestination(event.target.value); setPreview(null); setError('') }} placeholder="Default: ~/.claude/agents" className="min-w-0 flex-1 bg-transparent py-s font-mono text-sm text-on-surface outline-none placeholder:text-on-surface-low" />
        </span>
        <span data-type="caption" className="text-on-surface-low">Writes are limited to your home directory. Existing files are never replaced.</span>
      </label>
      {preview && <section aria-label="Export preview" className="grid gap-s rounded-lg border border-outline-variant/30 bg-surface-container/55 p-m">
        <h3 data-type="label-m" className="text-on-surface">Preview · {preview.destination}</h3>
        {preview.files.map(file => <p key={file.path} data-type="body-s" className="flex flex-wrap items-center justify-between gap-s font-mono text-on-surface-var"><span>{file.path} <span className="font-sans text-on-surface-low">· {file.bytes.toLocaleString()} bytes</span></span><span data-type="caption" className={file.status === 'foreign' ? 'text-danger' : file.status === 'replace' ? 'text-warn' : file.status === 'same' ? 'text-ok' : 'text-on-surface-low'}>{file.status === 'replace' ? 'replace Gideon file' : file.status}</span></p>)}
        {preview.conflicts.length > 0 && <p role="alert" data-type="body-s" className="text-warn">Foreign files will never be overwritten: {preview.conflicts.join(', ')}</p>}
        {preview.blocked.length > 0 && <p role="alert" data-type="body-s" className="text-danger">Export is blocked by the content safety scan. Remove the flagged content in Gideon and preview again.</p>}
        {preview.preview_token && <p data-type="caption" className="inline-flex items-center gap-s text-on-surface-low"><ShieldCheck size={14} /> This preview expires in {Math.ceil((preview.expires_in ?? 0) / 60)} minutes and is invalidated if agent content or destination changes.</p>}
      </section>}
      {written && <p role="status" data-type="body-s" className="rounded-lg border border-ok/30 bg-ok/10 p-m text-on-surface">{written.length ? `Export complete. Wrote ${written.join(', ')}.` : 'Export complete. All selected files were already current.'}</p>}
      {error && <p role="alert" data-type="body-s" className="text-danger">{error}</p>}
      <footer className="flex flex-wrap justify-end gap-s border-t border-outline-variant/25 pt-m">
        <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
        {eligible.length > 0 && !written && <>
          <Button variant="secondary" size="sm" loading={busy && !preview} disabled={selected.length === 0 || busy} onClick={() => void makePreview()}>{preview ? 'Refresh preview' : 'Preview export'}</Button>
          {preview?.preview_token && <Button size="sm" loading={busy && !!preview} disabled={busy} onClick={() => void writeExport()}><Download size={14} /> Export files</Button>}
        </>}
      </footer>
    </div>
  </Modal>
}
