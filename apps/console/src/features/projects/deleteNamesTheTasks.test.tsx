import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const WEB = process.cwd()
const REPO = join(WEB, '../..')
const page = ['ProjectsSection.tsx', 'projectCollectionState.ts'].map(path => readFileSync(join(WEB, 'src/features/projects', path), 'utf8')).join('\n')
const code = page
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

const plainBody = code.match(/title: `Delete project[\s\S]*?body: '([^']*)'/)?.[1] ?? ''
const forceBody = code.match(/title: 'Project still has active work'[\s\S]*?body: `([^`]*)`/)?.[1] ?? ''

describe('the plain delete dialog names the task loss', () => {
  it('found the dialog body', () => {
    expect(plainBody, 'the confirm body must still be a single-quoted literal here').not.toBe('')
  })

  it('🔴 the false promise is GONE', () => {
    expect(plainBody, 'it must no longer claim the tasks survive').not.toMatch(/tasks themselves stay/i)
    expect(plainBody).not.toMatch(/tasks?[^.]{0,30}(stay|survive|are kept|remain)/i)
  })

  it('it says the tasks are deleted, permanently', () => {
    expect(plainBody).toMatch(/task/i)
    expect(plainBody, 'permanence is the part that changes the decision').toMatch(/permanently|cannot be undone/i)
  })

  it('and it names what inside a task is lost, not just "tasks"', () => {
    expect(plainBody, 'the user-authored content is the real loss').toMatch(/notes/i)
    expect(plainBody).toMatch(/exit criteria/i)
  })

  it('🪤 it interpolates NO count', () => {
    const dialogArgs = code.match(/title: `Delete project[\s\S]*?danger: true/)?.[0] ?? ''
    expect(dialogArgs, 'found the dialog options').not.toBe('')
    expect(dialogArgs, 'no count is interpolated').not.toMatch(/\.length/)
  })

  it('🪤 the one true reassurance is KEPT', () => {
    expect(plainBody).toMatch(/[Ww]orkspace files on disk are left untouched/)
  })
})

describe('the force re-confirm names it too — the same cascade runs on that path', () => {
  it('found the force dialog body', () => {
    expect(forceBody, 'the 409 re-confirm must still be a template literal here').not.toBe('')
  })

  it('🪤 it was INCOMPLETE rather than wrong, and that is why it survived', () => {
    expect(forceBody, 'the task loss is named').toMatch(/task/i)
    expect(forceBody).toMatch(/permanently/i)
  })

  it('and its existing enumeration is intact — this change only adds', () => {
    for (const clause of [/bound loops/i, /worktrees/i, /UNBOUND/, /can't be undone/i]) {
      expect(forceBody, `the force dialog must keep: ${clause}`).toMatch(clause)
    }
  })
})

describe('VACUITY: the cascade these dialogs warn about is real', () => {
  const py = readFileSync(join(REPO, 'runtime/gideon/engine/tasks/hierarchy_handlers.py'), 'utf8')

  function retirement(): string {
    const handler = py.match(/async def api_projects_delete[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(handler, 'found the delete handler').not.toBe('')
    expect(handler, 'the public route delegates to project retirement').toMatch(/return await ProjectRetirement\(request\)\.respond\(\)/)
    const owner = py.match(/class ProjectRetirement:[\s\S]*?(?=\nclass |\ndef |$)/)?.[0] ?? ''
    expect(owner, 'found the project retirement owner').not.toBe('')
    return owner
  }

  it('the delete handler really does delete every task in the project', () => {
    const cascade = retirement().match(/    async def remove_tasks\(self\):[\s\S]*?(?=\n    async def |\n    def |$)/)?.[0] ?? ''
    expect(cascade, 'found the cascade method').not.toBe('')
    expect(cascade, 'the task scope is the project being deleted').toMatch(/project = _store\(\)\.get_project\(self\.project_id\)/)
    expect(cascade, 'and the window reached the complete project sweep').toMatch(/list_all_tasks\(project=project\.name, limit=10_000\)/)
    expect(cascade, 'it deletes each one').toMatch(/for task in tasks:[\s\S]*?await registry\.delete_task\(task\.id\)/)
  })

  it('and the provider really unlinks the file — there is nothing to restore from', () => {
    // Follow the provider's dispatch to the synchronous mutation owner; neither may turn soft.
    const native = readFileSync(join(REPO, 'runtime/gideon/engine/tasks/native.py'), 'utf8')
    const dele = native.match(/async def delete_task[\s\S]*?(?=\n    async def |\n    def |$)/)?.[0] ?? ''
    expect(dele, 'found delete_task').not.toBe('')
    expect(dele, 'the provider dispatches to task mutation').toMatch(/asyncio\.to_thread\(TaskMutation\(self\)\.delete, task_id\)/)
    const owner = native.match(/class TaskMutation:[\s\S]*?(?=\nclass |$)/)?.[0] ?? ''
    expect(owner, 'found the task mutation owner').not.toBe('')
    const mutation = owner.match(/    def delete\(self, identifier\):[\s\S]*?(?=\n    def |\n@dataclass|$)/)?.[0] ?? ''
    expect(mutation, 'found the actual delete operation').not.toBe('')
    expect(mutation, 'the unlinked path belongs to the requested task').toMatch(/path = self\.provider\._task_path\(identifier\)/)
    expect(mutation, 'a hard unlink, not a soft delete').toMatch(/path\.unlink\(\)/)
  })

  it('🔑 the cascade is deliberate, and this change does not touch it', () => {
    // Deleting the project first would leave its tasks unreachable from every scoped view.
    // The lifecycle owner makes that rationale executable by finishing the sweep first.
    const response = retirement().match(/    async def respond\(self\):[\s\S]*?(?=\n    async def |\n    def |$)/)?.[0] ?? ''
    expect(response, 'found the lifecycle response').not.toBe('')
    expect(response, 'bound-work admission still precedes the cascade').toMatch(/await self\.detach\(\)/)
    expect(response, 'the cascade runs before deleting the project').toMatch(/await self\.remove_tasks\(\)[\s\S]*?_store\(\)\.delete_project\(self\.project_id\)/)
  })
})
