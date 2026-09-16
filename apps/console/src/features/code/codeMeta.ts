export function codeDeleteBody(p: { name: string; status: string; workspace_dir?: string }): string {
  const running = ['running', 'planning', 'intake'].includes(p.status)
  const lead = running
    ? `"${p.name}" is still working — deleting it stops the worker. `
    : `"${p.name}" will be removed. `
  const tasks = 'Its plan and every task under it are permanently deleted. '
  const files = p.workspace_dir
    ? 'Your workspace folder and the files in it are left untouched, but Gideon’s own '
      + 'gideon/task-* branches there are force-deleted along with their worktrees — anything committed '
      + 'on one and not yet merged is lost. Work you have merged is safe.'
    : 'This project keeps its files in its own managed folder — deleting it also removes those files. '
      + 'Move anything you want to keep out first.'
  return `${lead}${tasks}${files}`
}
