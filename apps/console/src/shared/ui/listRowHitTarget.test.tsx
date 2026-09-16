import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListRow } from './ListScaffold'



describe('ListRow hit target', () => {
  it('is a real <button>, not a role=button div', () => {
    const { container } = render(
      <ListRow onClick={() => {}} label="Deploy pipeline"><span>body</span></ListRow>,
    )
    const btn = screen.getByRole('button', { name: 'Deploy pipeline' })
    expect(btn.tagName).toBe('BUTTON')
    expect(container.firstElementChild?.getAttribute('role')).toBeNull()
  })

  it('does not CONTAIN the row content (the nested-interactive shape)', () => {
    render(
      <ListRow onClick={() => {}} label="Row name">
        <button type="button">nested action</button>
      </ListRow>,
    )
    const hit = screen.getByRole('button', { name: 'Row name' })
    const nested = screen.getByRole('button', { name: 'nested action' })
    expect(hit.contains(nested), 'the hit target must not be an ancestor of the row content').toBe(false)
    for (let el = nested.parentElement; el; el = el.parentElement) {
      expect(
        el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button',
        `a nested control must have no interactive ancestor (found <${el.tagName}>)`,
      ).toBe(false)
    }
  })

  it('owns exactly ONE tab stop per row', () => {
    const { container } = render(<ListRow onClick={() => {}} label="Row"><span>body</span></ListRow>)
    const wrapper = container.firstElementChild!
    expect(wrapper.getAttribute('tabindex')).toBe('-1')
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    expect(screen.getByRole('button', { name: 'Row' }).getAttribute('tabindex')).toBeNull()
  })

  it('fires onClick from the row body (bubbling), so the whole row stays clickable', () => {
    const onClick = vi.fn()
    render(<ListRow onClick={onClick} label="Row"><span>the body text</span></ListRow>)
    fireEvent.click(screen.getByText('the body text'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('lets a nested control stop the row from firing', () => {
    const onRow = vi.fn()
    const onNested = vi.fn()
    render(
      <ListRow onClick={onRow} label="Row">
        <button type="button" onClick={(e) => { e.stopPropagation(); onNested() }}>action</button>
      </ListRow>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'action' }))
    expect(onNested).toHaveBeenCalledTimes(1)
    expect(onRow, 'a control that stops propagation must not also trigger the row').not.toHaveBeenCalled()
  })

  it('adds no hit target to a NON-interactive row', () => {
    const { container } = render(<ListRow label="unused"><span>static</span></ListRow>)
    expect(container.querySelector('button')).toBeNull()
    expect(container.firstElementChild?.getAttribute('tabindex')).toBeNull()
  })
})

describe('the tasks list row and card carry the same overlay', () => {
  const src = readFileSync(join(process.cwd(), "src/features/tasks/TasksListPage.tsx"), 'utf8')

  it('both wrappers hand their tab stop to the shared hit-target primitive', () => {
    const overlays = [...src.matchAll(/<RowHitTarget label=\{/g)]
    expect(overlays.length, 'one for the list row, one for the card').toBeGreaterThanOrEqual(2)
    expect(src, 'through the primitive, not a bespoke element').toMatch(/import \{ RowHitTarget \}/)
    for (const m of overlays) {
      const tail = src.slice(m.index, m.index + 90)
      expect(tail, 'the name is derived from the task').toMatch(/t\.title/)
    }
  })

  it('neither wrapper claims a role, and both keep tabIndex -1', () => {
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const wrappers = [...code.matchAll(/<motion\.div[\s\S]{0,900}?onClick=\{onOpen\}([\s\S]{0,1400}?)>/g)]
    expect(wrappers.length, 'the list row and the card').toBeGreaterThanOrEqual(2)
    for (const w of wrappers) {
      expect(w[1], 'no role on the wrapper').not.toMatch(/role="button"/)
      expect(w[1]).toMatch(/tabIndex=\{-1\}/)
    }
  })

  it('the ring is drawn on the row, keyed off the overlay', () => {
    const rings = [...src.matchAll(/has-\[>button:focus-visible\]:ring-2 has-\[>button:focus-visible\]:ring-inset has-\[>button:focus-visible\]:ring-primary\b/g)]
    expect(rings.length, 'both wrappers').toBeGreaterThanOrEqual(2)
  })

  it('the select checkbox still names itself per row, so the two stops are distinguishable', () => {
    expect(src).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
  })
})


describe('every clickable non-interactive element has a keyboard route', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function tags(src: string, name: string) {
    const out: string[] = []
    const re = new RegExp(`<${name}(?=[\\s>])`, 'g')
    let m: RegExpExecArray | null
    while ((m = re.exec(src))) {
      let i = m.index + 1 + name.length, depth = 0, quote: string | null = null
      for (; i < src.length; i++) {
        const c = src[i]
        if (quote) { if (c === quote) quote = null; continue }
        if (c === '"' || c === "'" || c === '`') { quote = c; continue }
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) break
      }
      out.push(src.slice(m.index, i + 1))
    }
    return out
  }

  const DEFERRED: Record<string, string> = {
    'features/knowledge/KnowledgeCreatePage.tsx':
      'a file DROPZONE, not a row: the keyboard route is the focusable `sr-only` input inside it ' +
      '(cycle 169), policed by design/filePickerReachable.test.ts, so the div needs no role of its own',
  }

  const hits = () => {
    const found: { file: string; tag: string }[] = []
    for (const abs of walk(SRC)) {
      const src = readFileSync(abs, 'utf8')
      for (const name of ['div', 'li', 'article', 'motion\\.div']) {
        for (const tag of tags(src, name)) {
          if (!/onClick=/.test(tag) || !/cursor-pointer/.test(tag)) continue
          if (/stopPropagation/.test(tag)) continue
          found.push({ file: abs.slice(SRC.length + 1), tag })
        }
      }
    }
    return found
  }

  it('finds the population it is meant to police', () => {
    expect(hits().length, 'the clickable-row census must not go empty').toBeGreaterThanOrEqual(12)
  })

  it('each one is either a real control, or a documented deferral', () => {
    const bad: string[] = []
    for (const { file, tag } of hits()) {
      const declaresRole = /\brole=/.test(tag) && /tabIndex=\{[^}]*\b0\b/.test(tag)
      const usesPrimitive = /tabIndex=\{[^}]*-1/.test(tag) && readFileSync(join(SRC, file), 'utf8').includes('<RowHitTarget')
      if (declaresRole || usesPrimitive || file in DEFERRED || file === 'shared/ui/RowHitTarget.tsx') continue
      bad.push(file)
    }
    expect(bad, `mouse-only click targets: add <RowHitTarget label> + tabIndex={-1}, or record the reason in DEFERRED\n${bad.join('\n')}`)
      .toEqual([])
  })

  it('the two rows this cycle converged go through the primitive', () => {
    for (const rel of ['features/notifications/NotificationsPage.tsx', 'features/loops/LoopsListPage.tsx', 'shared/ui/NotificationBell.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel}: the hit target`).toMatch(/<RowHitTarget label=/)
      expect(src, `${rel}: the ring, keyed off the overlay`).toMatch(/has-\[>button:focus-visible\]:ring-2/)
    }
  })

  it('the deferrals stay honest — a listed file must still FAIL the criteria', () => {
    for (const rel of Object.keys(DEFERRED)) {
      const tags = hits().filter((h) => h.file === rel)
      expect(tags.length, `${rel} no longer matches the scan at all — drop it from DEFERRED`).toBeGreaterThan(0)
      const fixed = tags.some((h) => {
        const declaresRole = /\brole=/.test(h.tag) && /tabIndex=\{[^}]*\b0\b/.test(h.tag)
        const usesPrimitive = /tabIndex=\{[^}]*-1/.test(h.tag) && readFileSync(join(SRC, rel), 'utf8').includes('<RowHitTarget')
        return declaresRole || usesPrimitive
      })
      expect(fixed, `${rel} now satisfies the criteria — drop it from DEFERRED`).toBe(false)
    }
  })
})
