import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SidePanel } from './SidePanel'


const SRC = readFileSync(join(process.cwd(), "src/shared/ui/SidePanel.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

describe('a panel title is recoverable when it clips', () => {
  it('a string title becomes a tooltip', () => {
    render(
      <SidePanel title="Summarize the three saved long-reads into the weekly digest" onClose={() => {}}>
        <p>body</p>
      </SidePanel>,
    )
    const h = screen.getByRole('heading', { level: 2 })
    expect(h.getAttribute('title')).toBe('Summarize the three saved long-reads into the weekly digest')
  })

  it('a NODE title gets no tooltip rather than "[object Object]"', () => {
    render(
      <SidePanel title={<span>Composed <b>title</b></span>} onClose={() => {}}>
        <p>body</p>
      </SidePanel>,
    )
    const h = screen.getByRole('heading', { level: 2 })
    expect(h.getAttribute('title')).toBeNull()
  })

  it('the guard is the type check, not a truthiness test', () => {
    expect(CODE).toMatch(/title=\{typeof title === 'string' \? title : undefined\}/)
  })

  it('the heading still truncates, and still names the region', () => {
    expect(CODE).toMatch(/data-type="title-l" className="text-on-surface truncate"/)
    expect(CODE).toMatch(/aria-labelledby=\{titleId\}/)
  })

  it('this is genuinely the shared panel — many surfaces mount it', () => {
    const consumers = walk(join(process.cwd(), "src"))
      .filter((abs) => /<SidePanel[\s>]/.test(readFileSync(abs, 'utf8')))
    expect(consumers.length, 'surfaces mounting a SidePanel').toBeGreaterThanOrEqual(15)
  })
})
