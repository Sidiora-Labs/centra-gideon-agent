import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { confirmDelete } from './index'
import { closeDialog, subscribeDialogs } from './dialogStore'
import { rowSubject } from '../../data/rowSubject'


const SRC = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

function objectAt(src: string, i: number): string {
  let depth = 0
  for (let j = i; j < Math.min(src.length, i + 1600); j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}') {
      depth--
      if (depth === 0) return src.slice(i, j + 1)
    }
  }
  return src.slice(i, i + 1600)
}

function dialogs(): Array<{ file: string; obj: string }> {
  const out: Array<{ file: string; obj: string }> = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(/confirm\(\s*\{/g)) {
      const brace = src.indexOf('{', m.index!)
      out.push({ file: abs.replace(SRC + '/', ''), obj: objectAt(src, brace) })
    }
  }
  return out
}

describe('a destructive confirm explains the consequence', () => {
  it('the canonical helper still supplies a body by default', () => {
    const helper = readFileSync(join(SRC, "shared/ui", 'dialog', 'index.ts'), 'utf8')
    expect(helper).toContain("body: opts?.body ?? 'This cannot be undone.'")
    expect(helper, 'and it is still the danger-tinted path').toContain('danger: true')
  })

  it('EVERY danger dialog carries a body — the ratchet', () => {
    const all = dialogs()
    expect(all.length, 'the sweep must find the dialogs').toBeGreaterThanOrEqual(30)
    const danger = all.filter((d) => /danger:\s*true/.test(d.obj))
    expect(danger.length, 'and the danger subset').toBeGreaterThanOrEqual(15)
    const bodyless = danger
      .filter((d) => !/\bbody\s*[:,]/.test(d.obj))
      .map((d) => `${d.file}: ${(/title: ([^\n]*)/.exec(d.obj)?.[1] ?? '?').slice(0, 48)}`)
    expect(bodyless, 'a destructive dialog that only asks "are you sure?" explains nothing').toEqual([])
  })

  it('the episodic-memory delete uses the helper its siblings use', () => {
    const src = readFileSync(join(SRC, "features", 'settings', 'MemoryPanel.tsx'), 'utf8')
    expect(src, 'converged onto the canonical helper').toContain("confirmDelete('episodic memory'")
    expect(src, 'and no longer hand-rolls the same dialog').not.toMatch(
      /confirm\(\{\s*title: 'Delete this episodic memory\?'/,
    )
    expect(src).toContain("confirmDelete('memory'")
    expect(src).toContain("confirmDelete('lesson'")
  })

  it('clear-all names the TOTAL, not the filtered view', () => {
    const src = readFileSync(join(SRC, "features", 'notifications', 'NotificationsPage.tsx'), 'utf8')
    expect(src).toContain('const total = items?.length ?? 0')
    expect(src, 'the count must come from the raw list, not `filtered`').not.toMatch(
      /const total = filtered/,
    )
    expect(src).toContain('`Clear all ${total} notification${total === 1 ? \'\' : \'s\'}?`')
    expect(src, 'and the body must say where they go').toMatch(
      /removed from disk[\s\S]{0,80}cannot be undone/,
    )
    expect(src, 'including the ones the filter is hiding').toContain('hidden by the current filter')
  })

  it('the dialogs that deliberately OMIT irreversibility keep doing so', () => {
    const inbox = readFileSync(join(SRC, "features", 'inbox', 'InboxPage.tsx'), 'utf8')
    expect(inbox, 'dismiss-all already names its count').toMatch(/Dismiss all \$\{n\} pending item/)
    expect(inbox).not.toMatch(/Dismiss all[\s\S]{0,200}cannot be undone/)
    const run = readFileSync(join(SRC, "features", 'workflows', 'WorkflowRunDetail.tsx'), 'utf8')
    expect(run, 'cancel says what survives instead').toContain('Completed work is kept.')
  })
})


describe('a destructive confirm names the item', () => {
  function callAt(src: string, i: number): string {
    let depth = 0
    for (let j = i; j < Math.min(src.length, i + 600); j++) {
      if (src[j] === '(') depth++
      else if (src[j] === ')') {
        depth--
        if (depth === 0) return src.slice(i + 1, j)
      }
    }
    return src.slice(i + 1, i + 600)
  }

  function arity(args: string): number {
    if (!args.trim()) return 0
    let depth = 0
    let n = 1
    for (const ch of args) {
      if ('([{'.includes(ch)) depth++
      else if (')]}'.includes(ch)) depth--
      else if (ch === ',' && depth === 0) n++
    }
    return n
  }

  function code(abs: string): string {
    return readFileSync(abs, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
  }

  function calls(): Array<{ file: string; args: string }> {
    const out: Array<{ file: string; args: string }> = []
    for (const abs of walk(SRC)) {
      const src = code(abs)
      for (const m of src.matchAll(/\bconfirmDelete\(/g)) {
        out.push({
          file: abs.replace(SRC + '/', ''),
          args: callAt(src, src.indexOf('(', m.index!)),
        })
      }
    }
    return out
  }

  it('NO caller omits the subject — the ratchet', () => {
    const all = calls()
    expect(all.length, 'the sweep must find the call sites').toBeGreaterThanOrEqual(21)
    const nameless = all.filter((c) => arity(c.args) < 2).map((c) => `${c.file}: confirmDelete(${c.args})`)
    expect(nameless, 'a delete dialog that asks about "this <thing>" identifies nothing').toEqual([])
  })

  it('the three prose subjects converge on rowSubject, not a hand-rolled slice', () => {
    const memory = readFileSync(join(SRC, "features", 'settings', 'MemoryPanel.tsx'), 'utf8')
    expect(memory).toContain("confirmDelete('episodic memory', rowSubject([selected.episodic.text], 40)")
    expect(memory).toContain("confirmDelete('lesson', rowSubject([selected.lesson.rule], 40)")
    expect(memory, 'imported, not redefined').toMatch(/import \{ rowSubject \} from '\.\.\/\.\.\/shared\/data\/rowSubject'/)
    const task = readFileSync(join(SRC, "features", 'tasks', 'TaskDetail.tsx'), 'utf8')
    expect(task).toContain("confirmDelete('comment', rowSubject([body], 40)")
    for (const c of calls()) {
      expect(c.args, `${c.file} truncates its own subject`).not.toMatch(/\.slice\(\s*0\s*,/)
    }
  })

  it('TaskDetail both takes the comment AND is passed it', () => {
    const src = code(join(SRC, "features", 'tasks', 'TaskDetail.tsx'))
    expect(src, 'the signature takes it').toMatch(/async function remove\(commentId: string, body: string\)/)
    expect(src, 'and the call site supplies it from the row it is rendering').toContain('remove(c.id, c.body)')
    expect(src, 'no caller left on the old one-argument form').not.toMatch(/remove\(c\.id\)/)
  })

  it('the helper still degrades to "this <entity>" on an empty subject', () => {
    const helper = readFileSync(join(SRC, "shared/ui", 'dialog', 'index.ts'), 'utf8')
    expect(helper).toContain('const label = name ? `${entity} "${name}"` : `this ${entity}`')
  })
})

describe('the sentence the dialog actually shows', () => {
  async function titleOf(open: () => Promise<boolean>): Promise<{ title: string; body: unknown }> {
    let seen: { id: number; title: string; body: unknown } | undefined
    const stop = subscribeDialogs((ds) => {
      const top = ds[ds.length - 1]
      if (top) seen = { id: top.id, title: top.title, body: top.body }
    })
    const p = open()
    if (!seen) throw new Error('no dialog opened')
    closeDialog(seen.id, false)
    await p
    stop()
    return { title: seen.title, body: seen.body }
  }

  it('names a lesson by its rule, which is what the delete call takes as identity', async () => {
    const rule = 'Prefer an explicit timeout over the default retry when a worker calls the gateway'
    const { title, body } = await titleOf(() => confirmDelete('lesson', rowSubject([rule], 40)))
    expect(title).toBe('Delete lesson "Prefer an explicit timeout over the def…"?')
    expect(body, 'and the helper still supplies the consequence').toBe('This cannot be undone.')
  })

  it('names an episodic memory by its text', async () => {
    const text = 'Asked me to stop summarising the roadmap and just take the next atom'
    const { title } = await titleOf(() => confirmDelete('episodic memory', rowSubject([text], 40)))
    expect(title).toBe('Delete episodic memory "Asked me to stop summarising the roadma…"?')
  })

  it('names a comment by its body, on one line even when it was typed on several', async () => {
    const commentBody = 'Blocked on the\n\n  ledger extraction landing first — see #1288'
    const { title } = await titleOf(() => confirmDelete('comment', rowSubject([commentBody], 40)))
    expect(title).toBe('Delete comment "Blocked on the ledger extraction landin…"?')
    expect(title, 'no newline reaches the title').not.toMatch(/\n/)
  })

  it('the header can hold the longer title — it wraps, it does not clip', () => {
    const shell = readFileSync(join(SRC, "shared/ui", 'dialog', 'DialogShell.tsx'), 'utf8')
    const titleLine = shell.split('\n').find((l) => l.includes('data-type="title-l"')) ?? ''
    expect(titleLine, 'the title line must be found').toContain('{title}')
    expect(titleLine, 'no truncation on a title that now carries the subject').not.toMatch(
      /truncate|whitespace-nowrap|line-clamp/,
    )
    expect(shell, 'and its column can shrink to wrap inside the sheet').toContain('min-w-0 flex-1')
    expect(shell).toContain("aria-label={typeof title === 'string' ? title : undefined}")
  })

  it('falls back to "this <entity>" when there is genuinely nothing to name', async () => {
    const { title } = await titleOf(() => confirmDelete('lesson', rowSubject([''])))
    expect(title).toBe('Delete this lesson?')
  })
})
