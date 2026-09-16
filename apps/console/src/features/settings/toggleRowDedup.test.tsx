import { describe, expect, it, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ToggleRow } from './settingsUI'


const SETTINGS = join(process.cwd(), "src/features/settings")

describe('ToggleRow lives in settingsUI only', () => {
  it('no panel declares a private copy', () => {
    const definers = readdirSync(SETTINGS)
      .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f) && f !== 'settingsUI.tsx')
      .filter((f) => /function ToggleRow\b/.test(readFileSync(join(SETTINGS, f), 'utf8')))
    expect(definers, `private ToggleRow in: ${definers.join(', ')}`).toEqual([])
  })

  it('all five migrated panels import it', () => {
    for (const f of ['SourcesPanel', 'LegibilityPanel', 'AmbientPanel', 'PacksPanel', 'AgentDefaultsPanel']) {
      const src = readFileSync(join(SETTINGS, `${f}.tsx`), 'utf8')
      expect(src, `${f} should import ToggleRow from ./settingsUI`)
        .toMatch(/import \{[^}]*\bToggleRow\b[^}]*\} from '\.\/settingsUI'/)
    }
  })
})

describe('ToggleRow behaviour', () => {
  const patchFor = () => vi.fn()

  it('reads its state from cfg[field]', () => {
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: true }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true')
  })

  it('treats a missing key as OFF rather than crashing', () => {
    const { container } = render(<ToggleRow label="Poll" cfg={{}} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('false')
  })

  it('coerces a truthy non-boolean to ON', () => {
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: 1 }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('[role="switch"]')?.getAttribute('aria-checked')).toBe('true')
  })

  it('patches the field with the new value and a flash callback', () => {
    const patch = patchFor()
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: false }} field="poll" patch={patch as never} />)
    fireEvent.click(container.querySelector('[role="switch"]')!)
    expect(patch).toHaveBeenCalledWith('poll', true, expect.any(Function), 'Poll')
  })

  it('shows the danger glyph only while ON', () => {
    const on = render(<ToggleRow label="YOLO" cfg={{ f: true }} field="f" patch={patchFor() as never} danger />)
    const off = render(<ToggleRow label="YOLO" cfg={{ f: false }} field="f" patch={patchFor() as never} danger />)
    expect(on.container.querySelector('svg.text-warn')).not.toBeNull()
    expect(off.container.querySelector('svg.text-warn')).toBeNull()
  })

  it('omits the danger glyph entirely when the prop is absent', () => {
    const { container } = render(<ToggleRow label="Poll" cfg={{ poll: true }} field="poll" patch={patchFor() as never} />)
    expect(container.querySelector('svg.text-warn')).toBeNull()
  })
})
