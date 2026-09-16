import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

function picksFirstOnEnter(): string[] {
  const out: string[] = []
  for (const abs of walk(SRC)) {
    const lines = strip(readFileSync(abs, 'utf8')).split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].includes("key === 'Enter'")) continue
      if (/\[0\]|\.at\(0\)/.test(lines.slice(i, i + 3).join('\n'))) { out.push(abs.replace(SRC + '/', '')); break }
    }
  }
  return out
}

describe('an Enter shortcut states its target', () => {
  it('the census is exactly the two palettes', () => {
    expect(picksFirstOnEnter().sort()).toEqual([
      'features/chat/PromptPalette.tsx',
      'features/code/WorkspacePicker.tsx',
    ])
  })

  it('each one says what Enter will do, with the same glyph the palette uses', () => {
    for (const rel of picksFirstOnEnter()) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must state the target`).toMatch(/<CornerDownLeft size=\{11\} \/> (picks|opens) the first/)
      expect(src, `${rel} must not claim a target when the list is empty`)
        .toMatch(/(?:!!filtered\?\.length|shownDirs\.length > 0) && \(?/)
    }
  })

  it('the wording matches what the handler actually does', () => {
    const prompt = strip(readFileSync(join(SRC, 'features/chat/PromptPalette.tsx'), 'utf8'))
    expect(prompt, 'picks filtered[0]').toMatch(/key === 'Enter'[\s\S]{0,120}pick\(filtered\[0\]\)/)
    expect(prompt).toContain('picks the first match')
    const ws = strip(readFileSync(join(SRC, 'features/code/WorkspacePicker.tsx'), 'utf8'))
    expect(ws, 'browses shownDirs[0]').toMatch(/key === 'Enter'[\s\S]{0,120}browse\(shownDirs\[0\]\.path\)/)
    expect(ws, 'and says so in the verb that surface uses').toContain('opens the first folder')
  })

  it('a form SUBMIT on Enter is not this defect — the falsified candidate', () => {
    const src = strip(readFileSync(join(SRC, 'features/tasks/TaskForm.tsx'), 'utf8'))
    expect(src, 'it binds Enter').toMatch(/key === 'Enter'/)
    expect(src, 'but to submit, not to pick out of a list').toMatch(/isProject \? createProject\(\) : createList\(\)/)
    expect(src, 'so it indexes nothing').not.toMatch(/key === 'Enter'[\s\S]{0,120}\[0\]/)
  })
})


vi.mock('../data/api', () => ({
  api: { prompts: vi.fn(async () => [
    { name: 'plan', title: 'Plan a change', description: 'first', variables: [] },
    { name: 'review', title: 'Review a diff', description: 'second', variables: [] },
  ]) },
}))

describe('PromptPalette renders the hint', () => {
  beforeEach(() => vi.clearAllMocks())

  it('tells the user what Enter picks, once the list has arrived', async () => {
    const { PromptPalette } = await import('../../features/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    expect(screen.getByText(/picks the first match/), 'the hint a keyboard user needs').toBeTruthy()
  })

  it('adds the escape hint only once there is a search to clear', async () => {
    const { PromptPalette } = await import('../../features/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    expect(screen.queryByText(/esc clears the search/), 'nothing to clear yet').toBeNull()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'plan' } })
    expect(screen.getByText(/esc clears the search/)).toBeTruthy()
  })

  it('says nothing about a first match when nothing matches', async () => {
    const { PromptPalette } = await import('../../features/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } })
    expect(screen.queryByText(/picks the first match/), 'the claim would be false here').toBeNull()
  })
})
