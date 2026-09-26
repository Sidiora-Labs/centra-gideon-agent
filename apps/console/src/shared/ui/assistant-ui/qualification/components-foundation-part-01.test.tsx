import './composer-contract.test'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GenerationLoader } from '../../../vendor/assistant-ui/elements/loading-state'
import { ThinkingIndicator } from '../../../vendor/assistant-ui/elements/thinking-indicator'
import { StreamingText } from '../../../vendor/assistant-ui/elements/streaming-text'
import { TypingIndicator } from '../../../vendor/assistant-ui/elements/typing-indicator'
import { ReasoningEffort } from '../../../vendor/assistant-ui/elements/reasoning-effort'
import { GuardrailNotice } from '../../../vendor/assistant-ui/elements/guardrail-notice'
import { MobileComposer } from '../../../vendor/assistant-ui/elements/mobile-composer'
import { announced, at, clamp, indexIn, pct, progressOf, take } from '../../../vendor/assistant-ui/utils/range'
import { ShimmerLabel, SwapLabel } from '../../../vendor/assistant-ui/elements/surfaces'

const slot = (container: HTMLElement, name: string) => {
  const element = container.querySelector(`[data-slot="${name}"]`)
  expect(element).not.toBeNull()
  return element as HTMLElement
}

describe('generation loader source behavior', () => {
  it.each(['dots', 'squares', 'rounded'] as const)('renders %s shape with the supplied label', variant => {
    const { container } = render(<GenerationLoader label="Generating" tick={0} variant={variant} data-run-id="run-1" />)
    const root = slot(container, 'generation-loader')
    expect(root.getAttribute('data-run-id')).toBe('run-1')
    expect(screen.getByText('Generating')).toBeInTheDocument()
    const cells = root.querySelectorAll('[aria-hidden] span')
    expect(cells).toHaveLength(9)
    const shape = variant === 'dots' ? 'rounded-full' : variant === 'squares' ? 'rounded-[1px]' : 'rounded-[3px]'
    expect(cells[0]).toHaveClass(shape, 'opacity-90')
    expect(cells[2]).toHaveClass('opacity-15')
  })

  it('advances the active cells with tick and preserves caller class', () => {
    const { container, rerender } = render(<GenerationLoader label="Working" tick={0} className="custom-loader" />)
    const root = slot(container, 'generation-loader')
    expect(root).toHaveClass('custom-loader')
    expect(root.querySelectorAll('.opacity-90')).toHaveLength(3)
    const before = Array.from(root.querySelectorAll('[aria-hidden] span')).map(node => node.classList.contains('opacity-90'))
    rerender(<GenerationLoader label="Working" tick={3} />)
    const after = Array.from(root.querySelectorAll('[aria-hidden] span')).map(node => node.classList.contains('opacity-90'))
    expect(after).not.toEqual(before)
  })
})

describe('thinking indicator source behavior', () => {
  it('shows active run text and elapsed time when known', () => {
    const { container } = render(<ThinkingIndicator label="Reading file" elapsed="3s" data-run-id="run-2" />)
    const root = slot(container, 'thinking-indicator')
    expect(root).toHaveAttribute('data-run-id', 'run-2')
    expect(within(root).getByText('Reading file')).toBeInTheDocument()
    expect(within(root).getByText('3s')).toBeInTheDocument()
    expect(root.querySelector('[aria-hidden]')).toBeInTheDocument()
  })

  it('does not invent elapsed time and updates the label', () => {
    const { container, rerender } = render(<ThinkingIndicator label="Starting" />)
    const root = slot(container, 'thinking-indicator')
    expect(root.textContent).toBe('Starting')
    rerender(<ThinkingIndicator label="Searching" />)
    expect(root.textContent).toBe('Searching')
  })
})

describe('streaming text source behavior', () => {
  const segments = [{ text: 'hello world' }, { text: 'const value', mono: true }]

  it('reveals exactly the requested words and marks fresh streamed words', () => {
    const { container } = render(<StreamingText segments={segments} count={3} streaming data-message-id="m1" />)
    const root = slot(container, 'streaming-text')
    expect(root).toHaveAttribute('data-message-id', 'm1')
    expect(root.textContent?.trim()).toBe('hello world const')
    expect(root.querySelectorAll(':scope > span')).toHaveLength(4)
    expect(root.querySelectorAll('.text-blue-500')).toHaveLength(2)
    expect(root.querySelector('.font-mono')).toHaveTextContent('const')
    expect(root.querySelector('[aria-hidden]')).toBeInTheDocument()
  })

  it('handles empty, negative, excessive and nonstreaming counts without a cursor', () => {
    const { container, rerender } = render(<StreamingText segments={segments} count={-2} streaming />)
    const root = slot(container, 'streaming-text')
    expect(root.textContent).toBe('')
    expect(root.querySelector('[aria-hidden]')).toBeNull()
    rerender(<StreamingText segments={segments} count={99} streaming={false} />)
    expect(root.textContent?.trim()).toBe('hello world const value')
    expect(root.querySelector('[aria-hidden]')).toBeNull()
    expect(root.querySelector('.text-blue-500')).toBeNull()
  })
})

describe('typing indicator source behavior', () => {
  it.each(['bare', 'bubble'] as const)('announces typing in %s layout', variant => {
    const { container } = render(<TypingIndicator variant={variant} data-message-id="m2" className="caller" />)
    const root = slot(container, 'typing-indicator')
    expect(root).toHaveAttribute('data-variant', variant)
    expect(root).toHaveAttribute('data-message-id', 'm2')
    expect(root).toHaveClass('caller')
    expect(screen.getByRole('status', { name: 'Assistant is typing' })).toBeInTheDocument()
    expect(root.querySelectorAll('[aria-hidden]')).toHaveLength(3)
    if (variant === 'bubble') expect(root).toHaveClass('rounded-full')
    else expect(root).toHaveAttribute('role', 'status')
  })

  it('defaults to a bubble', () => {
    const { container } = render(<TypingIndicator />)
    expect(slot(container, 'typing-indicator')).toHaveAttribute('data-variant', 'bubble')
  })
})

describe('reasoning effort source behavior', () => {
  const levels = [{ key: 'low', label: 'Low', budget: 100 }, { key: 'high', label: 'High', budget: 200 }]

  it('renders real selected budget, normalized progress, and actionable levels', () => {
    const onSelect = vi.fn()
    const { container } = render(<ReasoningEffort levels={levels} selectedKey="high" spent={50} onSelect={onSelect} />)
    const root = slot(container, 'reasoning-effort')
    expect(within(root).getByText('50 / 200')).toBeInTheDocument()
    const progress = within(root).getByRole('progressbar', { name: 'Thinking budget used' })
    expect(progress).toHaveAttribute('aria-valuenow', '25')
    expect(progress).toHaveAttribute('aria-valuetext', '50 of 200')
    expect(progress.querySelector('span')).toHaveStyle({ width: '25%' })
    expect(within(root).getByRole('button', { name: 'High' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(root).getByRole('button', { name: 'Low' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('low')
  })

  it('renders read-only level labels and handles missing budgets', () => {
    const { container } = render(<ReasoningEffort levels={levels} selectedKey="missing" spent={14} />)
    const root = slot(container, 'reasoning-effort')
    expect(within(root).queryAllByRole('button')).toHaveLength(0)
    expect(within(root).queryByText('14 / 0')).toBeNull()
    expect(within(root).queryByRole('progressbar')).toBeNull()
    expect(within(root).getByText('Low')).not.toHaveAttribute('aria-current')
  })

  it('clamps over-budget progress instead of emitting an invalid width', () => {
    const { container } = render(<ReasoningEffort levels={levels} selectedKey="low" spent={500} />)
    const progress = within(slot(container, 'reasoning-effort')).getByRole('progressbar')
    expect(progress).toHaveAttribute('aria-valuenow', '100')
    expect(progress.querySelector('span')).toHaveStyle({ width: '100%' })
  })
})

describe('guardrail notice source behavior', () => {
  const notice = { title: 'Blocked', explanation: 'Access denied', policy: 'P-2', alternatives: ['Summarize', 'Search'] }

  it('shows policy and invokes only the selected alternative', () => {
    const onPick = vi.fn()
    const { container } = render(<GuardrailNotice {...notice} onPick={onPick} data-policy-id="P-2" />)
    const root = slot(container, 'guardrail-notice')
    expect(root).toHaveAttribute('data-policy-id', 'P-2')
    expect(within(root).getByText('Blocked')).toBeInTheDocument()
    expect(within(root).getByText('Access denied')).toBeInTheDocument()
    expect(within(root).getByText('P-2')).toBeInTheDocument()
    fireEvent.click(within(root).getByRole('button', { name: 'Search' }))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('Search')
  })

  it('shows read-only alternatives without implying an action', () => {
    const { container } = render(<GuardrailNotice {...notice} />)
    const root = slot(container, 'guardrail-notice')
    expect(within(root).queryAllByRole('button')).toHaveLength(0)
    expect(within(root).getByText('Summarize')).toBeInTheDocument()
    expect(within(root).getByText('Search')).toBeInTheDocument()
  })

  it('omits the alternatives section when no alternatives exist', () => {
    const { container } = render(<GuardrailNotice {...notice} alternatives={[]} />)
    const root = slot(container, 'guardrail-notice')
    expect(within(root).queryByText('try instead')).toBeNull()
  })
})

describe('mobile composer editor contract', () => {
  const base = { value: 'hello', keyboardOpen: false, running: false, actions: [] as string[] }

  it('uses the donor input by default and sends on Enter', () => {
    const onSend = vi.fn()
    render(<MobileComposer {...base} onSend={onSend} />)
    const input = screen.getByRole('textbox', { name: 'Message' })
    expect(input).toHaveValue('hello')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it('mounts caller editor in place of the input, not alongside it', () => {
    const { container } = render(<MobileComposer {...base} editor={<div data-testid="codemirror-editor">Editor</div>} />)
    expect(within(slot(container, 'mobile-composer')).queryByRole('textbox')).toBeNull()
    expect(screen.getByTestId('codemirror-editor')).toBeInTheDocument()
  })
})


describe('range helpers used by donor elements', () => {
  it('clamps values to ordinary bounds and treats NaN as the lower bound', () => {
    expect(clamp(4, 0, 10)).toBe(4)
    expect(clamp(-4, 0, 10)).toBe(0)
    expect(clamp(14, 0, 10)).toBe(10)
    expect(clamp(Number.NaN, 2, 10)).toBe(2)
  })

  it('prefers the upper bound for empty or inverted collections', () => {
    expect(clamp(3, 1, 0)).toBe(0)
    expect(clamp(-3, 1, 0)).toBe(0)
  })

  it('takes no items for negative, NaN, or zero counts', () => {
    const items = ['alpha', 'beta', 'gamma']
    expect(take(items, -1)).toEqual([])
    expect(take(items, Number.NaN)).toEqual([])
    expect(take(items, 0)).toEqual([])
  })

  it('takes floor count up to collection length without modifying input', () => {
    const items = ['alpha', 'beta', 'gamma']
    expect(take(items, 1.9)).toEqual(['alpha'])
    expect(take(items, 22)).toEqual(items)
    expect(items).toEqual(['alpha', 'beta', 'gamma'])
  })

  it('normalizes indexes at both collection boundaries', () => {
    const items = ['a', 'b', 'c']
    expect(indexIn(items, -5)).toBe(0)
    expect(indexIn(items, 1.8)).toBe(1)
    expect(indexIn(items, 55)).toBe(2)
    expect(indexIn([], 4)).toBe(0)
  })

  it('returns a matching item or undefined for an empty collection', () => {
    const items = ['a', 'b', 'c']
    expect(at(items, -5)).toBe('a')
    expect(at(items, 1.8)).toBe('b')
    expect(at(items, 55)).toBe('c')
    expect(at([], 0)).toBeUndefined()
  })

  it('prevents division by zero and clamps percentages', () => {
    expect(pct(10, 0)).toBe(0)
    expect(pct(10, -2)).toBe(0)
    expect(pct(-10, 100)).toBe(0)
    expect(pct(250, 100)).toBe(100)
    expect(pct(35, 100)).toBe(35)
  })

  it('rounds announced percentages for stable assistive output', () => {
    expect(announced(33.33333333)).toBe(33.3)
    expect(announced(0)).toBe(0)
    expect(announced(100)).toBe(100)
  })

  it('clamps completed steps without exceeding the total', () => {
    expect(progressOf(2.8, 5)).toBe(2)
    expect(progressOf(-2, 5)).toBe(0)
    expect(progressOf(20, 5)).toBe(5)
    expect(progressOf(2, 0)).toBe(0)
    expect(progressOf(2, -1)).toBe(0)
  })
})

describe('shared visual primitives in the donor dependency closure', () => {
  it('applies shimmer by default and accepts caller attributes', () => {
    render(<ShimmerLabel data-testid="label" className="caller">Active</ShimmerLabel>)
    expect(screen.getByTestId('label')).toHaveClass('shimmer', 'caller')
    expect(screen.getByTestId('label')).toHaveTextContent('Active')
  })

  it('removes shimmer for an inactive label', () => {
    render(<ShimmerLabel active={false} data-testid="label">Done</ShimmerLabel>)
    expect(screen.getByTestId('label')).not.toHaveClass('shimmer')
    expect(screen.getByTestId('label')).toHaveTextContent('Done')
  })

  it('keeps both swap layers mounted while hiding the inactive one', () => {
    const { container, rerender } = render(<SwapLabel active={0} className="caller" children={['Idle', 'Busy']} />)
    const root = container.querySelector('.caller') as HTMLElement
    expect(root).toBeInTheDocument()
    expect(within(root).getByText('Idle')).toHaveAttribute('aria-hidden', 'false')
    expect(within(root).getByText('Busy')).toHaveAttribute('aria-hidden', 'true')
    rerender(<SwapLabel active={1} className="caller" children={['Idle', 'Busy']} />)
    expect(within(root).getByText('Idle')).toHaveAttribute('aria-hidden', 'true')
    expect(within(root).getByText('Busy')).toHaveAttribute('aria-hidden', 'false')
  })
})

describe('part-01 caller state transitions', () => {
  it('updates loader progress without replacing its source component', () => {
    const { container, rerender } = render(<GenerationLoader label="Queued" tick={2} />)
    const root = slot(container, 'generation-loader')
    const before = root.querySelectorAll('.opacity-90').length
    rerender(<GenerationLoader label="Running" tick={8} />)
    expect(slot(container, 'generation-loader')).toBe(root)
    expect(root).toHaveTextContent('Running')
    expect(root).not.toHaveTextContent('Queued')
    expect(root.querySelectorAll('.opacity-90')).toHaveLength(before)
  })

  it('keeps a static indicator when the elapsed label is removed', () => {
    const { container, rerender } = render(<ThinkingIndicator label="Reasoning" elapsed="12s" />)
    const root = slot(container, 'thinking-indicator')
    expect(root).toHaveTextContent('12s')
    rerender(<ThinkingIndicator label="Reasoning" />)
    expect(root).toHaveTextContent('Reasoning')
    expect(root).not.toHaveTextContent('12s')
  })

  it('uses plain typography for non-mono segments', () => {
    const { container, rerender } = render(<StreamingText segments={[{ text: 'plain word' }]} count={2} streaming={false} />)
    const root = slot(container, 'streaming-text')
    expect(root.querySelector('.font-mono')).toBeNull()
    rerender(<StreamingText segments={[{ text: 'plain word', mono: true }]} count={2} streaming={false} />)
    expect(root.querySelectorAll('.font-mono')).toHaveLength(2)
  })

  it('reconciles streaming cursor with the newest segment count', () => {
    const segments = [{ text: 'first second third' }]
    const { container, rerender } = render(<StreamingText segments={segments} count={1} streaming />)
    const root = slot(container, 'streaming-text')
    expect(root.querySelectorAll('[aria-hidden]')).toHaveLength(1)
    rerender(<StreamingText segments={segments} count={3} streaming />)
    expect(root).toHaveTextContent('first second third')
    expect(root.querySelectorAll('.text-blue-500')).toHaveLength(2)
    rerender(<StreamingText segments={segments} count={3} streaming={false} />)
    expect(root.querySelectorAll('[aria-hidden]')).toHaveLength(0)
  })

  it('changes typing layout while keeping one announced status', () => {
    const { container, rerender } = render(<TypingIndicator variant="bare" />)
    expect(screen.getAllByRole('status', { name: 'Assistant is typing' })).toHaveLength(1)
    rerender(<TypingIndicator variant="bubble" />)
    expect(screen.getAllByRole('status', { name: 'Assistant is typing' })).toHaveLength(1)
    expect(slot(container, 'typing-indicator')).toHaveAttribute('data-variant', 'bubble')
  })

  it('preserves fractional accessibility progress to one decimal', () => {
    const { container } = render(<ReasoningEffort levels={[{ key: 'a', label: 'A', budget: 3 }]} selectedKey="a" spent={1} />)
    const progress = within(slot(container, 'reasoning-effort')).getByRole('progressbar')
    expect(progress).toHaveAttribute('aria-valuenow', '33.3')
    expect(progress.querySelector('span')).toHaveStyle({ width: '33.33333333333333%' })
  })

  it('changes guardrail actions when callback becomes available', () => {
    const onPick = vi.fn()
    const notice = { title: 'Restricted', explanation: 'Policy applies', policy: 'P-3', alternatives: ['Use summary'] }
    const { rerender } = render(<GuardrailNotice {...notice} />)
    expect(screen.queryByRole('button', { name: 'Use summary' })).toBeNull()
    rerender(<GuardrailNotice {...notice} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: 'Use summary' }))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('Use summary')
  })
})
