import { useEffect, useRef, useState } from 'react'
import { api, type ProjectItem } from '../../shared/data/api'
import { invalidateKeys, useQuery } from '../../shared/data/data'
import { getActiveProject, setActiveProject } from '../../shared/data/activeProject'
import { confirm } from '../../shared/ui/dialog'

export type ProjectDraft = { name: string; brief: string; workspaceDir: string; setActive: boolean }
export function useActiveProjectId() {
  const [id, setId] = useState(getActiveProject)
  useEffect(() => {
    const refresh = () => setId(getActiveProject())
    refresh(); window.addEventListener('ne:active-project', refresh)
    return () => window.removeEventListener('ne:active-project', refresh)
  }, [])
  return id
}
async function removeProject(project: ProjectItem): Promise<boolean> {
  if (!await confirm({
    title: `Delete project "${project.name}"?`,
    body: 'Every task in this project is permanently deleted, along with its notes, action plan and exit criteria — that cannot be undone. Its context directory and task lists go with it. Workspace files on disk are left untouched.',
    danger: true, confirmLabel: 'Delete',
  })) return false
  let force = false
  for (;;) {
    try { await api.deleteProject(project.id, force); break }
    catch (failure) {
      if (force || (failure as { status?: number })?.status !== 409) throw failure
      if (!await confirm({
        title: 'Project still has active work',
        body: `"${project.name}" still has work scoped under it. Every task in the project is permanently deleted with its notes and exit criteria. Deleting it now also STOPS and REMOVES any bound loops (Goal, Code, Design, General) — their workers are halted, parallel git worktrees + branches cleaned up, and the loops deleted (not just unlinked). Project-bound chats are kept but UNBOUND (detached from this project, not deleted). This can't be undone.`,
        danger: true, confirmLabel: 'Delete anyway',
      })) return false
      force = true
    }
  }
  if (getActiveProject() === project.id) setActiveProject('')
  return true
}
export function useProjectCollection(onOpen: (id: string) => void) {
  const { data: projects, loading, error: loadErr, refresh } = useQuery('projects:list', () => api.projects(), { persist: true })
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const activeId = useActiveProjectId()
  const pending = useRef(new Set<string>())
  const reload = () => { invalidateKeys('projects:list'); refresh() }
  const create = async (draft: ProjectDraft) => {
    const name = draft.name.trim()
    if (!name || pending.current.has('create')) return
    pending.current.add('create'); setBusy(true); setErr(null)
    try {
      const project = await api.createProject({ name, name_locked: true, brief: draft.brief.trim() || undefined, workspace_dir: draft.workspaceDir.trim() || undefined })
      if (draft.setActive) setActiveProject(project.id)
      setCreating(false); reload(); onOpen(project.id)
    } catch (failure) { setErr((failure as Error)?.message || 'Could not create the project') }
    finally { pending.current.delete('create'); setBusy(false) }
  }
  const del = async (project: ProjectItem) => {
    if (pending.current.has(project.id)) return
    pending.current.add(project.id); setErr(null)
    try { if (await removeProject(project)) reload() }
    catch (failure) { setErr(`Couldn't delete that project: ${(failure as Error)?.message || 'unknown error'}`); reload() }
    finally { pending.current.delete(project.id) }
  }
  return { projects, loading, loadErr, refresh, reload, creating, setCreating, busy, err, setErr, activeId, create, del }
}
