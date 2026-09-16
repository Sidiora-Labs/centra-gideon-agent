import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { pyBetween, pyMethod } from '../../theme/pySource'


const SRC = join(process.cwd(), "src")
const PY = join(__dirname, "../../../../../../runtime/gideon")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const web = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')

describe('the task delete states what the backend really does', () => {
  it('claims the unblock, and the backend performs it', () => {
    const ui = web('features/tasks/TaskDetail.tsx')
    expect(ui, 'the sentence exists').toMatch(
      /waiting on it \$\{dependents\.length === 1 \? 'becomes' : 'become'\} unblocked/,
    )
    expect(ui, 'and the body interpolates it').toMatch(
      /body: `This cannot be undone\.\$\{unblocks\}`/,
    )
    const impl = py('engine/tasks/native.py')
    const del = impl.slice(impl.indexOf('async def delete_task'), impl.indexOf('def graph(self)'))
    expect(del, 'edges pointing at the deleted task are dropped').toMatch(
      /d\.depends_on_task_id != task_id/,
    )
    expect(del, 'and every former dependent is re-evaluated').toMatch(
      /reconcile\.reconcile_blocked_status\(tasks, t\.id\)/,
    )
  })

  it('the count comes from the set the Blocks section shows', () => {
    const ui = web('features/tasks/TaskDetail.tsx')
    expect(ui).toMatch(/const dependents = allTasks\.filter\(\(t\) => prereqIds\(t\)\.includes\(task\.id\)\)/)
    expect((ui.match(/allTasks\.filter\(\(t\) => prereqIds/g) ?? []).length, 'computed once').toBe(1)
  })

  it('names the task — it was the one dialog naming its subject nowhere', () => {
    const ui = web('features/tasks/TaskDetail.tsx')
    expect(ui).toMatch(/title: `Delete "\$\{task\.title\}"\?`/)
    expect(ui, 'the anonymous form must not come back').not.toMatch(/title: 'Delete this task\?'/)
  })

  it('says NOTHING about comments, because the backend does not remove them', () => {
    const impl = py('engine/tasks/native.py')
    const del = impl.slice(impl.indexOf('async def delete_task'), impl.indexOf('def graph(self)'))
    expect(del, 'still no comments cleanup — if this fails, update the dialog copy too')
      .not.toMatch(/_comments_/)
    expect(web('features/tasks/TaskDetail.tsx'), 'so the dialog claims nothing about them')
      .not.toMatch(/body: `This cannot be undone\.[^`]*comment/)
  })
})

describe('the knowledge delete states what the backend really takes', () => {
  it('claims the highlights, and the schema cascades them', () => {
    expect(web('features/knowledge/KnowledgeDetail.tsx'), 'the dialog counts them').toMatch(
      /Its \$\{annotations\.length\} highlight/,
    )
    const store = py('cognition/knowledge/store.py')
    const ddl = store.slice(store.indexOf('CREATE TABLE IF NOT EXISTS annotations'))
    const annotations = ddl.slice(0, ddl.indexOf(');'))
    expect(annotations, 'the annotations DDL must be found').toContain('quote TEXT NOT NULL')
    expect(annotations, 'and its item_id must cascade').toMatch(
      /item_id TEXT NOT NULL REFERENCES items\(id\) ON DELETE CASCADE/,
    )
    const connect = store.slice(store.indexOf('self.db = sqlite3.connect('))
    expect(connect.slice(0, 400), 'the connection itself enables foreign keys')
      .toMatch(/PRAGMA foreign_keys=ON/)
  })

  it('claims the stored file, and the handler unlinks it', () => {
    expect(web('features/knowledge/KnowledgeDetail.tsx')).toMatch(
      /The file stored in your library is deleted too\./,
    )
    const h = py('interfaces/dashboard/handlers/knowledge.py')
    const del = h.slice(h.indexOf('async def delete_item'), h.indexOf('async def delete_item_annotation'))
    expect(del, 'the tracked paths').toMatch(/victims = \[item\.get\("file_path"\), item\.get\("thumbnail_path"\)\]/)
    expect(del, 'and the derived media artifacts').toMatch(/files_root\.glob\(f"\{item_id\}\.\*"\)/)
    expect(del, 'fenced to the library files dir').toMatch(/files_root = Path\(knowledge_files_dir\(\)\)\.resolve\(\)/)
  })

  it('both clauses are conditional — no claim about what this item does not have', () => {
    const ui = web('features/knowledge/KnowledgeDetail.tsx')
    expect(ui, 'highlights clause gated on there being some').toMatch(/annotations\.length > 0\s*\n?\s*\?/)
    expect(ui, 'file clause gated on there being a file').toMatch(/item\.file_path \?/)
    expect(ui, 'and the body composes both').toMatch(
      /body: `This removes it from the knowledge base\.\$\{highlights\}\$\{stored\}`/,
    )
  })
})

describe('no destructive dialog names its subject NOWHERE', () => {
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
    })

  function objectAt(src: string, i: number): string {
    let depth = 0
    for (let j = i; j < Math.min(src.length, i + 1600); j++) {
      if (src[j] === '{') depth++
      else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(i, j + 1) }
    }
    return src.slice(i, i + 1600)
  }

  it('every hand-rolled danger dialog identifies what it is about', () => {
    const SINGLE_SUBJECT_SURFACES = [
      'features/code/CodeCockpitPage.tsx',
      'features/files/browse/FileViewer.tsx',
      'features/workflows/WorkflowRunDetail.tsx',
    ]
    const anonymous: string[] = []
    for (const abs of walk(SRC)) {
      if (SINGLE_SUBJECT_SURFACES.includes(abs.replace(SRC + '/', ''))) continue
      const src = strip(readFileSync(abs, 'utf8'))
      for (const m of src.matchAll(/confirm\(\s*\{/g)) {
        const obj = objectAt(src, src.indexOf('{', m.index!))
        if (!/danger:\s*true/.test(obj)) continue
        const titleAndBody = (obj.match(/title:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
          + (obj.match(/body:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
        if (!titleAndBody.includes('${')) {
          anonymous.push(`${abs.replace(SRC + '/', '')}: ${(obj.match(/title: ([^\n]*)/)?.[1] ?? '?').slice(0, 46)}`)
        }
      }
    }
    expect(anonymous, 'a dialog that identifies its subject in neither title nor body').toEqual([])
  })

  it('each single-subject surface still holds exactly one such dialog', () => {
    const counts: Record<string, number> = {}
    for (const rel of ['features/code/CodeCockpitPage.tsx', 'features/files/browse/FileViewer.tsx',
      'features/workflows/WorkflowRunDetail.tsx']) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      let n = 0
      for (const m of src.matchAll(/confirm\(\s*\{/g)) {
        const obj = objectAt(src, src.indexOf('{', m.index!))
        if (!/danger:\s*true/.test(obj)) continue
        const titleAndBody = (obj.match(/title:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
          + (obj.match(/body:[\s\S]*?(?=\n\s*\w+:|$)/)?.[0] ?? '')
        if (!titleAndBody.includes('${')) n++
      }
      counts[rel] = n
    }
    expect(counts).toEqual({
      'features/code/CodeCockpitPage.tsx': 1,
      'features/files/browse/FileViewer.tsx': 1,
      'features/workflows/WorkflowRunDetail.tsx': 1,
    })
  })
})

describe('two more bodies: one corrected, one confirmed', () => {
  it('the bulk dismiss says what survives, and this page is what keeps them', () => {
    const ui = web('features/inbox/InboxPage.tsx')
    expect(ui, 'the whole sentence').toContain(
      'There is no undo — but they stay readable under Handled.',
    )
    expect(ui, "and the filter that makes the second half true").toMatch(
      /filter === 'handled' \? \(it\.status === 'handled' \|\| it\.status === 'sent' \|\| it\.status === 'dismissed'\)/,
    )
  })

  it('the no-undo half is still true — restore refuses a dismissed item', () => {
    const h = py('interfaces/dashboard/handlers_inbox.py')
    const restore = h.slice(h.indexOf('async def api_inbox_restore'))
    expect(restore.slice(0, 1400), 'only a FILTERED item can be restored').toMatch(
      /if item\.status != ItemStatus\.FILTERED\.value:[\s\S]{0,120}status=409/,
    )
    expect(web('features/inbox/InboxDetail.tsx'), 'the only Restore control is for the filtered case')
      .toMatch(/A second-opinion check flagged this claim/)
  })

  it('the chat delete really does take the history it promises', () => {
    expect(web('features/ChatPage.tsx')).toContain('and its history will be permanently removed.')
    const h = py('interfaces/dashboard/chat_handlers.py')
    const del = h.slice(h.indexOf('async def api_chat_session_delete'))
    expect(del.slice(0, 6000), 'the on-disk artifacts are purged').toMatch(
      /conversation_log\.delete_session\(history_key\)/,
    )
    expect(del.slice(0, 6000)).toMatch(/letting it resurrect on reopen/)
  })
})

describe('the project delete, and the two workflow bodies', () => {
  it('names the TASK deletion the handler performs — verified against the caller, not the callee', () => {
    const ui = web('features/projects/ProjectsSection.tsx')
    expect(ui, 'the task loss is named').toMatch(/Every task in this project is permanently deleted/)
    expect(ui, 'the original misleading word must not come back').not.toContain('task lists detached')
    expect(ui, 'and neither may the false reassurance that replaced it')
      .not.toContain('the tasks themselves stay')

    const handler = py('engine/tasks/hierarchy_handlers.py')
    const del = handler.match(/async def api_projects_delete[\s\S]*?(?=\nasync def |\ndef |$)/)?.[0] ?? ''
    expect(del, 'found the delete handler').not.toBe('')
    expect(del, 'it resolves every task in the project').toMatch(/list_all_tasks\(project=/)
    expect(del, 'and deletes each one').toMatch(/delete_task\(t\.id\)/)

    const h = pyMethod(py('engine/tasks/hierarchy.py'), '    def delete_project')
    expect(h, 'list files are unlinked, not detached').toMatch(
      /self\._list_path\(tl\.id\)\.unlink\(missing_ok=True\)/,
    )
    expect(h, 'and this FUNCTION genuinely does leave task rows to the provider').toMatch(
      /the task provider owns task deletion/,
    )
  })

  it('the force re-confirm names it too — the same cascade runs on that path', () => {
    const ui = web('features/projects/ProjectsSection.tsx')
    const force = ui.match(/title: 'Project still has active work'[\s\S]*?body: `([^`]*)`/)?.[1] ?? ''
    expect(force, 'found the force dialog').not.toBe('')
    expect(force, 'the task loss is named on the force path too').toMatch(/task/i)
    expect(force, 'and its existing enumeration survives').toMatch(/bound loops/i)
  })

  it('the workspace-untouched half holds for the dialog that says it', () => {
    const h = pyMethod(py('engine/tasks/hierarchy.py'), '    def delete_project')
    expect(h, 'the project dir goes wholesale').toMatch(/shutil\.rmtree\(self\._project_dir\(project_id\)/)
    const handler = py('engine/tasks/hierarchy_handlers.py')
    expect(handler, 'bound work is refused without force').toMatch(
      /rmtree its worktrees out\s*\n?\s*#?\s*from under git/,
    )
    expect(web('features/projects/ProjectsSection.tsx'), 'and the force path has its own warning')
      .toMatch(/STOPS and REMOVES any bound loops/)
  })

  it('a workflow run really does keep its own spec copy', () => {
    expect(web('features/workflows/WorkflowsListPage.tsx')).toContain(
      'Existing runs keep their own copy of the spec and are unaffected.',
    )
    expect(py('automation/workflows/service.py'), 'the run persists its own spec at start')
      .toMatch(/store\.write_spec\(run\.id, spec\)/)
  })

  it('cancel stops the run without deleting what finished', () => {
    expect(web('features/workflows/WorkflowRunDetail.tsx')).toContain('In-flight steps are stopped. Completed work is kept.')
    const cancel = py('automation/workflows/service.py').slice(
      py('automation/workflows/service.py').indexOf('def cancel_run('),
      py('automation/workflows/service.py').indexOf('async def delete_run('),
    )
    expect(cancel, 'cancel does not delete').not.toMatch(/delete|rmtree|unlink/)
    expect(py('automation/workflows/service.py'), 'deletion is its own deliberate operation')
      .toMatch(/async def delete_run\(/)
  })
})

describe('four more bodies, all already true — pinned so they stay that way', () => {
  it('the tag delete really re-parents children instead of deleting the branch', () => {
    const ui = web('features/knowledge/TagManager.tsx')
    expect(ui).toContain('become top-level rather than being deleted')
    const store = py('cognition/knowledge/store.py')
    const ddl = store.slice(store.indexOf('CREATE TABLE IF NOT EXISTS tags'))
    expect(ddl.slice(0, ddl.indexOf(');')), 'the self-FK sets null').toMatch(
      /parent_id INTEGER REFERENCES tags\(id\) ON DELETE SET NULL/,
    )
    const connect = store.slice(store.indexOf('self.db = sqlite3.connect('))
    expect(connect.slice(0, 400), 'and the connection enforces foreign keys').toMatch(/PRAGMA foreign_keys=ON/)
    expect(ui).toMatch(/This removes the tag from \$\{t\.usage_count\} item/)
    expect(pyMethod(store, '    def delete_tag'), 'the docstring states the same contract')
      .toMatch(/Children are re-parented to root rather than deleted/)
  })

  it('the shelf delete leaves the items alone', () => {
    expect(web('features/knowledge/KnowledgeListPage.tsx')).toContain(
      'The shelf goes away. The items on it stay in your library.',
    )
    const del = pyMethod(py('cognition/knowledge/store.py'), '    def delete_collection')
    expect(del, 'membership rows and the collection row go').toMatch(/DELETE FROM collection_items/)
    expect(del, 'and the items table is never touched').not.toMatch(/DELETE FROM items/)
  })

  it('the skill delete really removes the directory', () => {
    expect(web('features/skills/SkillInspector.tsx')).toContain('This removes it from disk. This cannot be undone.')
    expect(pyMethod(py('extensions/skills/loader.py'), '    def delete_skill'), 'rmtree, not a registry flag')
      .toMatch(/shutil\.rmtree\(skill_dir\)/)
  })

  it('the Ollama delete really reaches the host — across the app boundary', () => {
    expect(web('features/settings/OllamaModelManager.tsx')).toContain(
      "This frees disk on the Ollama host and can't be undone.",
    )
    const h = py('interfaces/dashboard/handlers/providers.py')
    expect(h, 'core delegates to the catalog').toMatch(/await catalog\.delete_model\(model\)/)
    expect(h, 'and only for a provider whose catalog can do it').toMatch(/isinstance\(catalog, ModelManager\)/)
  })
})

describe('the stop-project dialog, and the file delete', () => {
  it('warns that a running task loses its worktree — the half that costs work', () => {
    const ui = web('features/code/CodeCockpitPage.tsx')
    expect(ui).toContain('a task still running loses its own worktree and branch')
    expect(ui, 'and it now says how kept work got there').toContain('already merged into your workspace is kept')
    const wt = pyMethod(py('automation/loop/worktree.py'), 'def cleanup_all')
    expect(wt, 'the worktree is force-removed').toMatch(/"worktree", "remove", "--force"/)
    expect(wt, 'and its branch force-deleted').toMatch(/"branch", "-D", branch_name\(name\)/)
    expect(py('automation/loop/kinds/sdlc.py'), 'a finished task merges into the workspace')
      .toMatch(/worktree\.merge_worktree\(ws, tid/)
  })

  it('stop really is terminal, which is why the warning matters', () => {
    const stop = pyMethod(py('automation/loop/manager.py'), 'async def stop')
    expect(stop, 'teardown then a terminal status').toMatch(/_teardown\(svc, loop_id\)[\s\S]{0,200}LoopStatus\.STOPPED/)
    expect(pyMethod(py('automation/loop/manager.py'), 'async def _teardown'), 'and teardown is what cleans worktrees')
      .toMatch(/worktree\.cleanup_all\(loop\.workspace_dir/)
  })

  it('the folder delete really recurses', () => {
    const ui = web('features/files/FilesSection.tsx')
    expect(ui).toContain('This deletes the folder and all its contents. This cannot be undone.')
    expect(ui).toMatch(/entry\.is_dir \? 'This deletes the folder and all its contents/)
    const h = py('interfaces/dashboard/handlers/files.py')
    const del = h.slice(h.indexOf('async def api_file_delete'))
    expect(del.slice(0, 2200), 'a directory is rmtree-d').toMatch(/shutil\.rmtree\(path\)/)
    expect(del.slice(0, 2200), 'and a root is refused').toMatch(/refusing to delete a root directory/)
  })
})

describe('the conflict-resolve body, per choice', () => {
  const ui = () => web('features/settings/DurabilityPanel.tsx')

  it('promises reversibility ONLY for the choice that earns it', () => {
    expect(ui()).toMatch(/choice === 'keep_local'/)
    expect(ui(), 'the reversible branch names the other side').toContain(
      "The other machine's version stays in the shared store, so you can still decide differently from that side.",
    )
    expect(ui(), 'and the destructive branch says what is gone').toContain(
      "That copy is not kept anywhere else — only a snapshot has it.",
    )
    expect(ui(), 'the unconditional promise must not come back').not.toContain(
      "The version you don't pick stays in the shared store",
    )
  })

  it('the backend pushes nothing, which is what makes the keep_local half true', () => {
    const mod = py('operations/durability/conflict_resolve.py')
    expect(mod, 'resolving is a LOCAL write').toMatch(/Nothing is pushed from here/)
    const keepLocal = pyBetween(mod, '``keep_local``', '``take_remote``')
    expect(keepLocal, 'the keep_local bullet must be found').toMatch(/three shas are unchanged/)
    expect(keepLocal, 'the divergence is detected and held again').toMatch(/HOLDS the\s+id again/)
  })

  it('take_remote and accept_proposal really do overwrite this machine\'s row', () => {
    const mod = py('operations/durability/conflict_resolve.py')
    expect(pyBetween(mod, '``take_remote``', '``accept_proposal``'), 'take_remote converges onto the remote sha')
      .toMatch(/local becomes the remote sha/)
    const proposal = mod.slice(mod.indexOf('``accept_proposal``'))
    expect(proposal.slice(0, proposal.indexOf('"' + '""')), 'accept_proposal writes a third sha')
      .toMatch(/local becomes a THIRD sha/)
    expect(pyMethod(mod, 'def _write_chosen_row'), 'the old row is simply replaced')
      .toMatch(/Substitute ``row`` for ``entity_id``/)
  })

  it('a resolved record is never silently re-applied — the other half of "decide again"', () => {
    expect(py('operations/durability/conflict_resolve.py')).toMatch(
      /``already_resolved``\s*the record was reviewed already \(never re-applied silently\)/,
    )
  })
})

describe('the last three bodies, and what this sweep does NOT claim', () => {
  it('the intent delete really takes what it gathered', () => {
    const ui = web('features/knowledge/KnowledgeListPage.tsx')
    expect(ui).toContain('Everything it gathered goes with it')
    expect(ui, 'and the no-outcomes branch says only the intent goes').toContain(
      'It has gathered nothing yet, so only the intent itself goes.',
    )
    const h = py('interfaces/dashboard/handlers/knowledge.py')
    const del = h.slice(h.indexOf('async def delete_intent('), h.indexOf('async def list_intent_outcomes'))
    expect(del, 'outcomes are deleted with it').toMatch(/delete_intent_outcomes\(intent_id\)/)
  })

  it('the theme delete really falls back to the default when the theme was active', () => {
    const ui = web('features/settings/DesignPanel.tsx')
    expect(ui).toContain('You are using this theme, so the app goes back to its default colors.')
    expect(ui, 'and the inactive branch says what a theme IS').toContain('a saved theme is a file, not a snapshot')
    const app = web('app/shell/appearance.tsx')
    expect(app, 'the active scheme reverts to the default').toMatch(
      /p\.scheme === id\s*\n?\s*\? \{ \.\.\.p, scheme: DEFAULT_SCHEME/,
    )
    expect(app, 'and the theme itself is a deleted file').toMatch(/await api\.deleteTheme\(slug\)/)
  })

  it('records the two bodies this sweep deliberately did NOT decide', () => {
    expect(web('features/settings/MultiInstanceCard.tsx'), 'still the default body')
      .toMatch(/confirmDelete\('instance', inst\.display_name \|\| inst\.id\)/)
    expect(web('features/settings/LocalModelManager.tsx'), 'still the default body')
      .toMatch(/confirmDelete\('model', name\)/)
    expect(py('extensions/providers/use_cases.py'), 'pruning keys on the provider NAME, not the instance')
      .toMatch(/str\(r\)\.split\(":", 1\)\[0\] in known/)
  })
})

describe('three more bodies, checked against their handlers', () => {
  it('the provider delete states the selections it drops, and the handler drops them', () => {
    const ui = web('features/settings/ModelBackends.tsx')
    expect(ui, 'the clause exists').toContain('Any use case set to one of its models loses that selection.')
    expect(ui, 'and the body composes it').toMatch(
      /body: `Models it provides will no longer be available\.\$\{selections\}\$\{key\}`/,
    )
    const h = py('interfaces/dashboard/handlers/providers.py')
    const del = h.slice(h.indexOf('async def api_provider_delete'), h.indexOf('async def api_provider_test'))
    expect(del, 'the handler drops the active-model refs').toMatch(/_drop_provider_active_models\(name\)/)
    const dropper = h.slice(h.indexOf('def _drop_provider_active_models'))
    expect(dropper.slice(0, 700), 'across every use case').toMatch(/for use_case, refs in list\(active\.items\(\)\)/)
  })

  it('the credential clause is conditional AND true — the handler never touches the store', () => {
    const ui = web('features/settings/ModelBackends.tsx')
    expect(ui, 'gated on a credential actually being stored').toMatch(
      /provider\.credential_status === 'ok' \? ' Its saved credential stays in the store\.'/,
    )
    const h = py('interfaces/dashboard/handlers/providers.py')
    const del = h.slice(h.indexOf('async def api_provider_delete'), h.indexOf('async def api_provider_test'))
    expect(del, 'no credential deletion in the delete path').not.toMatch(
      /credential|keyring|secret|delete_key|remove_credential/i,
    )
  })

  it('the schedule delete really removes the run history it promises', () => {
    expect(web('features/schedule/ScheduleDetail.tsx')).toContain('Its run history is removed too.')
    const h = py('interfaces/dashboard/handlers/triggers.py')
    const del = h.slice(h.indexOf('if request.method == "DELETE":'))
    expect(del.slice(0, 3000), 'the second half of the delete').toMatch(
      /await _runs_store\(\)\.delete_for_job\(raw\)/,
    )
  })

  it('the MCP remove needs no extra clause — checked, and a hypothesis killed', () => {
    const h = py('interfaces/dashboard/handlers/mcp.py')
    expect(h, 'the canonical store is Gideon-scoped').toMatch(
      /def _canonical_mcp_json[\s\S]{0,200}?return config_dir\(\) \/ "mcp\.json"/,
    )
    const fn = h.slice(h.indexOf('async def api_mcp_server_detail'))
    const del = fn.slice(fn.indexOf('if request.method == "DELETE":'), fn.indexOf('# PUT — register or update'))
    expect(del, 'the delete branch must be found').toMatch(/for store in \(/)
    expect(del, 'and it does not write the claude-code config').not.toMatch(/_CC_GLOBAL_JSON/)
    expect(web('features/tools/ToolsPage.tsx'), 'so the body stays as it is')
      .toContain('Its tools will no longer be available.')
  })
  it('the two project deletes ride the shared ritual, with a custom destruction body', () => {
    for (const rel of ['features/code/CodeSection.tsx', 'features/code/CodeCockpitPage.tsx']) {
      const src = web(rel)
      expect(src, `${rel} rides the shared ritual with a custom body`)
        .toMatch(/confirmDelete\('project', p\.name, \{ body: \w+\(p\) \}\)/)
      expect(src, `${rel} keeps no hand-rolled project-delete dialog`)
        .not.toMatch(/confirm\(\{ title: `Delete project/)
      expect(src, `${rel} must not hand-roll the body`).not.toContain('left untouched')
    }
    expect(web('features/code/codeMeta.ts'), 'the greenfield warning survives the hoist')
      .toContain('deleting it also removes those files')
  })
})
