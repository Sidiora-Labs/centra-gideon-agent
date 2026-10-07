import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const WEB = process.cwd()
const REPO = join(WEB, '../..')
const panel = readFileSync(join(WEB, 'src/features/settings/MemoryPanel.tsx'), 'utf8')
const code = panel
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')

const calls = [...code.matchAll(/confirmDelete\(\s*'([^']+)'[\s\S]{0,600}?\)\)\)/g)]

describe('the two reversible deletes say they are reversible', () => {
  it('found all four deletes', () => {
    expect(calls.map((m) => m[1])).toEqual(['memory', 'episodic memory', 'lesson', 'entity'])
  })

  it('🔴 the semantic-fact delete no longer claims irreversibility', () => {
    const fact = calls[0][0]
    expect(fact, 'a custom body is passed').toMatch(/body:/)
    expect(fact, 'and it names the undo').toMatch(/reversible/i)
    expect(fact, 'pointing at where the undo lives').toMatch(/History tab/)
  })

  it('🪤 the lesson delete says reversible AND names the confidence reset', () => {
    const lesson = calls[2][0]
    expect(lesson, 'a custom body is passed').toMatch(/body:/)
    expect(lesson).toMatch(/History tab/)
    expect(lesson, 'the caveat that makes "undo" honest here').toMatch(/confidence reset/)
  })

  it('the entity delete explains permanent retirement without promising undo', () => {
    expect(calls[3][0]).toContain('Its name stays retired')
    expect(calls[3][0]).not.toMatch(/reversible|History tab/)
  })

  it('🔑 the EPISODIC delete keeps the default — it really cannot be undone', () => {
    const ep = calls[1][0]
    expect(ep, 'episodic must NOT gain a body claiming reversibility').not.toMatch(/body:/)
  })
})

describe('VACUITY: the undo this copy promises actually exists', () => {
  const vm = readFileSync(join(REPO, 'runtime/gideon/cognition/vector_memory.py'), 'utf8')

  it('delete_semantic is a tombstone that keeps the value, not a row deletion', () => {
    const fn = vm.match(/def delete_semantic\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found delete_semantic').not.toBe('')
    expect(fn).toContain('SemanticRetirement(self).tombstone(key, source)')
    const retirement = readFileSync(join(REPO, 'runtime/gideon/cognition/archive_semantics.py'), 'utf8').split('def tombstone(')[1].split('def chain(')[0]
    expect(retirement, 'a tombstone, not a DELETE').toMatch(/is_deleted = 1/)
    expect(retirement).not.toMatch(/DELETE FROM semantic_memory/)
    expect(retirement, 'the prior value is journaled').toMatch(/_log_event\([\s\S]*?"delete"[\s\S]*?"semantic"[\s\S]*?previous\["value_json"\]/)
  })

  it('a lesson delete routes through it, which is why it shares the undo', () => {
    const fn = vm.match(/def delete_lesson\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found delete_lesson').not.toBe('')
    expect(fn).toContain('LessonCatalogue(self).forget(rule_substring)')
    const forget = readFileSync(join(REPO, 'runtime/gideon/cognition/archive_lessons.py'), 'utf8').split('def forget(')[1].split('def prompt(')[0]
    expect(forget, 'it delegates to the semantic tombstone').toMatch(/self\.store\.delete_semantic\(/)
    expect(forget, 'and it voids the earned observations').toMatch(/self\.store\._reverse_lesson\(/)
  })

  it('the UI really offers Undo for a semantic delete', () => {
    expect(code, "'delete' is undoable").toMatch(/const UNDOABLE = new Set\(\[[^\]]*'delete'/)
    expect(code, 'gated to semantic events, which is why episodic has no route back')
      .toMatch(/ev\.memory_type === 'semantic' && UNDOABLE\.has\(ev\.event_type\)/)
  })

  it('🔑 undo_event refuses a NON-semantic event — the episodic copy rests on this', () => {
    const fn = vm.match(/def undo_event\(self[\s\S]*?(?=\n    def )/)?.[0] ?? ''
    expect(fn, 'found undo_event').not.toBe('')
    expect(fn).toContain('ArchiveJournal(self).undo(event_id)')
    const undo = readFileSync(join(REPO, 'runtime/gideon/cognition/archive_events.py'), 'utf8').split('def undo(')[1].split('def mark_reversed(')[0]
    expect(undo).toMatch(/if kind != "semantic":[\s\S]*?return False/)
  })

  it("the default body really is the irreversibility claim, so taking it was the defect", () => {
    const dialog = readFileSync(join(WEB, 'src/shared/ui/dialog/index.ts'), 'utf8')
    expect(dialog).toMatch(/body: opts\?\.body \?\? 'This cannot be undone\.'/)
  })
})
