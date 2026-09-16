const KEY = 'active-project'

export function getActiveProject(): string {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}

export function setActiveProject(projectId: string): void {
  try {
    if (projectId) localStorage.setItem(KEY, projectId)
    else localStorage.removeItem(KEY)
  } catch {   }
  try { window.dispatchEvent(new CustomEvent('ne:active-project', { detail: projectId })) } catch {   }
}
