import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { PromptBindings } from '../../shared/data/api'
import { PromptsPanel } from './PromptsPanel'


const promptBindings = vi.fn()
const setPromptBinding = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    promptBindings: (...a: unknown[]) => promptBindings(...a),
    setPromptBinding: (...a: unknown[]) => setPromptBinding(...a),
  },
}))

const PAYLOAD: PromptBindings = {
  use_cases: ['chat', 'nl_to_cron', 'cycle_judge'],
  default_ref: 'native:system-chat',
  categories: [
    { key: 'agent', label: 'Agent system prompts', hint: 'The default-agent system prompt for a runtime context.' },
    { key: 'internal', label: 'Internal task prompts', hint: 'One-shot LLM tasks the system runs on your behalf.' },
    { key: 'loop', label: 'Loop & orchestration prompts', hint: 'Autonomous loop and orchestration prompts.' },
  ],
  bindings: [
    {
      use_case: 'chat', ref: '', effective_ref: 'native:system-chat',
      label: 'Chat', hint: 'Interactive sessions — dashboard, Slack, CLI', category: 'agent',
    },
    {
      use_case: 'nl_to_cron', ref: '', effective_ref: 'native:task-nl-to-cron',
      label: 'Natural language → cron',
      hint: 'Convert a natural-language scheduling request into a 5-field cron expression.',
      category: 'internal',
    },
    {
      use_case: 'cycle_judge', ref: 'native:system-chat', effective_ref: 'native:system-chat',
      label: 'Cycle judge', hint: 'Scores one loop cycle.', category: 'loop',
    },
  ],
  available: [{ name: 'system-chat', title: 'System chat', kind: 'system' } as PromptBindings['available'][number]],
}

describe('every runtime context is named, described and grouped', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    promptBindings.mockResolvedValue(PAYLOAD)
  })

  it('renders one heading per group the backend sent, in that order', async () => {
    render(<PromptsPanel />)
    await screen.findByRole('heading', { name: 'Agent system prompts', level: 2 })
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Agent system prompts', 'Internal task prompts', 'Loop & orchestration prompts'])
  })

  it('a task context shows its human label and its description, not its key', async () => {
    render(<PromptsPanel />)
    expect(await screen.findByText('Natural language → cron')).toBeTruthy()
    expect(screen.getByText('Convert a natural-language scheduling request into a 5-field cron expression.')).toBeTruthy()
    expect(screen.queryByText('nl_to_cron')).toBeNull()
    expect(screen.queryByText('cycle_judge')).toBeNull()
  })

  it("the picker's accessible name is the label, which is what a screen reader announces", async () => {
    render(<PromptsPanel />)
    expect(await screen.findByLabelText('Prompt for Natural language → cron')).toBeTruthy()
    expect(screen.getByLabelText('Prompt for Cycle judge')).toBeTruthy()
    expect(screen.queryByLabelText('Prompt for nl_to_cron')).toBeNull()
  })

  it('a group with no rows renders no heading', async () => {
    promptBindings.mockResolvedValue({
      ...PAYLOAD,
      bindings: PAYLOAD.bindings.filter((b) => b.category !== 'loop'),
    })
    render(<PromptsPanel />)
    await screen.findByRole('heading', { name: 'Agent system prompts', level: 2 })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Loop & orchestration prompts' })).toBeNull()
    })
  })

  it('the panel holds no label table of its own', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), "src/features/settings/PromptsPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'no local use-case → label map').not.toMatch(/USE_CASE_LABEL|Record<string, \{ title/)
    expect(src, 'the label comes from the binding').toMatch(/\{binding\.label\}/)
    expect(src, 'and so does the hint').toMatch(/binding\.hint/)
  })
})
