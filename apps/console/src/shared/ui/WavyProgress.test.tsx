import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { WavyProgress } from './WavyProgress'


describe('WavyProgress', () => {
  it('names a determinate bar and reports its value', () => {
    const { container } = render(<WavyProgress value={0.42} label="Downloading llama3" />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('role')).toBe('progressbar')
    expect(svg.getAttribute('aria-label')).toBe('Downloading llama3')
    expect(svg.getAttribute('aria-valuenow')).toBe('42')
    expect(svg.getAttribute('aria-valuemin')).toBe('0')
    expect(svg.getAttribute('aria-valuemax')).toBe('100')
  })

  it('clamps the reported value to the bar it draws', () => {
    const over = render(<WavyProgress value={1.4} label="x" />).container.querySelector('svg')!
    const under = render(<WavyProgress value={-0.3} label="x" />).container.querySelector('svg')!
    expect(over.getAttribute('aria-valuenow')).toBe('100')
    expect(under.getAttribute('aria-valuenow')).toBe('0')
  })

  it('leaves the indeterminate wave hidden and roleless', () => {
    const { container } = render(<WavyProgress />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('aria-hidden')).not.toBeNull()
    expect(svg.getAttribute('role')).toBeNull()
    expect(svg.getAttribute('aria-valuenow')).toBeNull()
  })

  it('the only call site names its determinate bar and leaves the other bare', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/LocalModelManager.tsx"), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    expect(code, 'determinate bars name the model').toMatch(/<WavyProgress width=\{200\} value=\{frac\} label=\{`Downloading \$\{m\.name\}`\}/)
    expect(code, 'the indeterminate one stays unnamed').toMatch(/<WavyProgress width=\{200\} \/>/)
  })

  it('the doc records the pairing, because the drift guard reads it', () => {
    const doc = readFileSync(join(process.cwd(), "src/shared/ui/WavyProgress.doc.ts"), 'utf8')
    expect(doc).toMatch(/name: 'label'/)
    expect(doc).toMatch(/aria-hidden on purpose|aria-hidden deliberately/)
  })
})
