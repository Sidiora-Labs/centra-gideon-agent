import { useEffect, useState } from 'react'
import { api, ApiError, type ProjectKnowledgeItem } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'

type Linked = Awaited<ReturnType<typeof api.projectLinked>> & { knowledge: ProjectKnowledgeItem[] }
const emptyLinked = (): Linked => ({ loops: [], code: [], chats: [], artifacts: [], knowledge: [] })
export function useProjectPeek(id: string) {
  const [linked, setLinked] = useState<Linked | null>(null)
  const [taskLists, setTaskLists] = useState<Awaited<ReturnType<typeof api.taskLists>>>([])
  useEffect(() => {
    let live = true
    setLinked(null); setTaskLists([])
    const loadWork = async () => {
      let value = emptyLinked()
      try { const result = await api.projectLinked(id); value = { ...result, knowledge: result.knowledge ?? [] } } catch {}
      if (live) setLinked(value)
    }
    const loadLists = async () => {
      try { const lists = await api.taskLists(id); if (live) setTaskLists(lists) } catch {}
    }
    void loadWork(); void loadLists()
    return () => { live = false }
  }, [id])
  return { linked, taskLists }
}
export function useProjectDirectory(root: string) {
  const [cur, setCur] = useState(root)
  const { data, loading, error } = useQuery(`dirtree:${cur}`, () => api.fileList(cur))
  const relative = cur.startsWith(root) ? cur.slice(root.length).replace(/^\//, '') : ''
  const crumbs = relative ? relative.split('/') : []
  const entries = data?.entries ?? []
  const ordered = [...entries.filter(entry => entry.is_dir), ...entries.filter(entry => !entry.is_dir)]
  return { cur, setCur, data, loading, error, crumbs, entries, ordered }
}
export function projectDirectoryError(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0
  const messages: Record<number, string> = { 404: 'This folder no longer exists on disk.', 400: 'This folder is outside the area Gideon can browse.', 403: 'This folder is outside the area Gideon can browse.' }
  return messages[status] ?? "Couldn't read this directory."
}
