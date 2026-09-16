import { useRef, useState } from 'react'
import { api, type ProjectItem, type TaskListItem } from '../../shared/data/api'
import { invalidateKeys, useQuery } from '../../shared/data/data'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { notify } from '../../app/shell/appSdk'
import { useActiveProjectId } from './projectCollectionState'
export type ProjectPanel = { kind: 'tasks'; list: TaskListItem } | { kind: 'dir'; label: string; path: string }
function resolvePanel(token: string, lists: TaskListItem[] | undefined, project: ProjectItem | undefined): ProjectPanel | null {
  const separator = token.indexOf(':')
  if (separator < 0) return null
  const category = token.slice(0, separator), identity = token.slice(separator + 1)
  if (category === 'tasks') { const list = lists?.find(entry => entry.id === identity); return list ? { kind: 'tasks', list } : null }
  if (category !== 'dir') return null
  const targets = { workspace: { label: 'Workspace', path: project?.workspace_dir }, context: { label: 'Context', path: project?.context_dir } }
  const target = targets[identity as keyof typeof targets]
  return target?.path ? { kind: 'dir', label: target.label, path: target.path } : null
}
export function useProjectDetailState(id: string, query: RouteProps['query'], setQuery: RouteProps['setQuery']) {
  const { data: project, loading, error: detailErr, refresh } = useQuery(`projects:detail:${id}`, () => api.project(id))
  const { data: lists } = useQuery(`projects:lists:${id}`, () => api.taskLists(id))
  const { data: work, loading: workLoading } = useQuery(`projects:work:${id}`, () => api.projectWork(id), { persist: true })
  const { data: config } = useQuery('config:gideon', () => api.gideonConfig())
  const [panelToken, setPanelToken] = useQueryParam(query, setQuery, 'panel', '')
  const [renaming, setRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [pickWs, setPickWs] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const pending = useRef(false)
  const active = useActiveProjectId() === id
  const patch = async (body: Record<string, unknown>) => {
    setErr(null)
    try { await api.updateProject(id, body); [`projects:detail:${id}`, 'projects:list'].forEach(key => invalidateKeys(key)); refresh() }
    catch (failure) { setErr((failure as Error)?.message || 'Could not update the project') }
  }
  const regenerateContext = async () => {
    if (pending.current) return
    pending.current = true; setRegenerating(true); setErr(null)
    try {
      const result = await api.regenerateContextAdapters(id)
      notify(result.errors.length ? `Wrote ${result.written.length}, ${result.errors.length} failed — see ${result.errors[0].file}` : `Context files refreshed (${result.written.length}) in ${result.workspace_dir}`, result.errors.length ? 'error' : 'success')
    } catch (failure) { setErr((failure as Error)?.message || 'Could not refresh context files') }
    finally { pending.current = false; setRegenerating(false) }
  }
  return { project, loading, detailErr, refresh, lists, work, workLoading, renaming, setRenaming, nameDraft, setNameDraft, pickWs, setPickWs, regenerating, err, setErr, active, patch, regenerateContext,
    adaptersEnabled: Boolean((config as { legibility?: { context_adapters?: boolean } } | undefined)?.legibility?.context_adapters),
    panel: resolvePanel(panelToken, lists, project),
    setPanel: (panel: ProjectPanel | null) => setPanelToken(!panel ? '' : panel.kind === 'tasks' ? `tasks:${panel.list.id}` : `dir:${panel.path === project?.context_dir ? 'context' : 'workspace'}`),
  }
}
