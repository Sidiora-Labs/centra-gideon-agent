import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import { ChecklistEditor } from './formControls'


type Item = { description: string; done?: boolean }

function renderEditor(items: Item[]) {
  return render(
    <ChecklistEditor<Item>
      items={items}
      onChange={() => {}}
      doneKey="done"
      placeholder="Add a step"
    />,
  )
}

describe('the checklist tick has a name that says what it does and what state it is in', () => {
  it('an UNTICKED row names the action and the row — it used to announce bare "button"', () => {
    renderEditor([{ description: 'write the migration' }])
    expect(screen.getByRole('button', { name: 'Mark done: write the migration' })).toBeTruthy()
  })

  it('a TICKED row announces the opposite action — this is how state is conveyed', () => {
    renderEditor([{ description: 'write the migration', done: true }])
    expect(screen.getByRole('button', { name: 'Mark not done: write the migration' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Mark done: write the migration' })).toBeNull()
  })

  it('each row gets its OWN name, so four rows are four distinct buttons', () => {
    renderEditor([{ description: 'alpha' }, { description: 'beta' }, { description: 'gamma', done: true }])
    expect(screen.getByRole('button', { name: 'Mark done: alpha' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mark done: beta' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Mark not done: gamma' })).toBeTruthy()
  })

  it('an empty description still yields a usable name rather than a dangling colon-only label', () => {
    renderEditor([{ description: '' }])
    expect(screen.getByRole('button', { name: /^Mark done:/ })).toBeTruthy()
  })
})

describe('the tick meets the pointer-target floor without moving the row', () => {
  const src = readFileSync(join(process.cwd(), "src/features/tasks/formControls.tsx"), 'utf8')
  const tick = src.match(/<button type="button" onClick=\{\(\) => toggle\(i\)\}[\s\S]*?<\/button>/)?.[0] ?? ''

  it('the tick markup was found — every assertion below depends on it', () => {
    expect(tick, 'the tick button must be located before it can be measured').not.toBe('')
  })

  it('the target is 24px, not 20px', () => {
    expect(tick).toMatch(/\bsize-6\b/)
    expect(tick, '20px is under the pointer-target floor').not.toMatch(/\bsize-5\b/)
  })

  it('and the extra 4px is reclaimed horizontally so the layout does not shift', () => {
    expect(tick, 'a bare size-6 would jog the row 4px').toMatch(/-mx-0\.5/)
    expect(tick, 'a vertical reclaim would overlap the stacked target above').not.toMatch(/-my-/)
  })

  it('the spacers that align to it are untouched, which is what the reclaim buys', () => {
    expect(src).toMatch(/<span className="size-5 shrink-0" \/>/)
  })
})
