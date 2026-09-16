
import { describe, expect, it } from 'vitest'
import { Suspense } from 'react'
import { render, waitFor } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { SHELL_ELEMENTS, getShellElement, PERSONALITIES } from './personalities'

const SRC = join(process.cwd(), "src")
const IDS = Object.keys(SHELL_ELEMENTS) as (keyof typeof SHELL_ELEMENTS)[]

async function mountEntry(id: keyof typeof SHELL_ELEMENTS): Promise<HTMLElement> {
  const Element = SHELL_ELEMENTS[id]
  const { container } = render(
    <Suspense fallback={null}>
      <Element />
    </Suspense>,
  )
  await waitFor(() => expect(container.firstElementChild).not.toBeNull())
  return container.firstElementChild as HTMLElement
}

describe('the registry is closed', () => {
  it('has at least one entry (without which every assertion below is vacuous)', () => {
    expect(IDS.length).toBeGreaterThan(0)
  })

  it('getShellElement refuses an id that is not a member', () => {
    expect(getShellElement('terminal-scanlines-v2')).toBeNull()
    expect(getShellElement('../ui/personality/TerminalStrip')).toBeNull()
    expect(getShellElement('')).toBeNull()
    expect(getShellElement(undefined)).toBeNull()
    for (const inherited of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf']) {
      expect(getShellElement(inherited), inherited).toBeNull()
    }
  })

  it('resolves every registered id to its own entry', () => {
    for (const id of IDS) expect(getShellElement(id)).toBe(SHELL_ELEMENTS[id])
  })

  it('no personality declares an id outside the registry', () => {
    for (const p of PERSONALITIES) {
      const id = p.behavior.shellElement
      if (id) expect(IDS as string[], `${p.id} → ${id}`).toContain(id)
    }
  })
})

describe('every entry is decorative — asserted from the rendered DOM', () => {
  it.each(IDS)('%s is aria-hidden, pointer-events-none, and marks its own id', async (id) => {
    const root = await mountEntry(id)
    expect(root.getAttribute('aria-hidden'), 'aria-hidden').toBe('true')
    expect(root.className, 'pointer-events-none').toContain('pointer-events-none')
    expect(root.getAttribute('data-shell-element'), 'data-shell-element').toBe(id)
    expect(root.querySelectorAll('a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .toHaveLength(0)
  })
})

describe('lazy means lazy', () => {
  it('every registry value is a React lazy component, not a direct reference', () => {
    const LAZY = Symbol.for('react.lazy')
    for (const id of IDS) {
      const entry = SHELL_ELEMENTS[id] as unknown as { $$typeof?: symbol }
      expect(entry.$$typeof, `${id} must be lazy()`).toBe(LAZY)
    }
  })

  it('only the registry references ui/personality/, and only dynamically', () => {
    const files = walkSource(SRC)
    expect(files.length, 'source walk found nothing').toBeGreaterThan(100)

    const statics: string[] = []
    let dynamicRefs = 0
    for (const file of files) {
      const rel = relative(SRC, file).replace(/\\/g, '/')
      const code = stripComments(readFileSync(file, 'utf8'))
      if (/(?:^|\n)\s*(?:import|export)\s*(?:[^\n;]*?\bfrom\s*)?['"][^'"]*ui\/personality\//.test(code)) {
        statics.push(rel)
      }
      dynamicRefs += (code.match(/\bimport\s*\(\s*['"][^'"]*ui\/personality\//g) ?? []).length
    }

    expect(dynamicRefs, 'no dynamic import of ui/personality/ — nothing is code-split')
      .toBeGreaterThanOrEqual(IDS.length)
    expect(
      statics,
      `These modules statically import ui/personality/, which re-bundles a shell\n` +
        `element into their chunk and defeats lazy():\n${statics.join('\n')}`,
    ).toEqual([])
  })
})

function walkSource(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walkSource(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function stripComments(code: string): string {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}
