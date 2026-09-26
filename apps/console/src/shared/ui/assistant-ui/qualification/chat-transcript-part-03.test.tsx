import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MessageAssistant } from '../../chat/MessageAssistant'
import { MessageUser } from '../../chat/MessageUser'
import { clockTime } from '../../../data/epoch'

const savedAt = '2025-06-18T12:35:00Z'

describe('persisted message timing', () => {
  it('renders the authoritative timestamp from the assistant turn', () => {
    const view = render(<MessageAssistant timestamp={savedAt} actions={<button type="button">Real actions</button>}>Stored answer</MessageAssistant>)
    const timing = view.container.querySelector('time')
    expect(timing).not.toBeNull()
    expect(timing?.textContent).toContain(clockTime(savedAt))
    expect(screen.getByText('Stored answer')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Real actions' })).toBeTruthy()
  })

  it('renders the same persisted timestamp for a saved user turn while preserving its body', () => {
    const view = render(<MessageUser timestamp={savedAt}>Open the project file</MessageUser>)
    const timing = view.container.querySelector('time')
    expect(timing?.textContent).toContain(clockTime(savedAt))
    expect(view.container.textContent).toContain('Open the project file')
  })

  it('does not invent a timestamp when the turn has none or an invalid value', () => {
    const view = render(<MessageAssistant>Streaming answer</MessageAssistant>)
    expect(view.container.querySelector('time')).toBeNull()
    view.rerender(<MessageAssistant timestamp="not a date">Streaming answer</MessageAssistant>)
    expect(view.container.querySelector('time')).toBeNull()
    view.rerender(<MessageAssistant timestamp={savedAt}>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('time')).not.toBeNull()
  })

  it('shows donor logo only for a model identified by the bound session', () => {
    const view = render(<MessageAssistant model="claude-sonnet-4">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('claude-sonnet-4')
    view.rerender(<MessageAssistant model="gemini-2.5-pro">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('gemini-2.5-pro')
    view.rerender(<MessageAssistant model="gpt-5">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('gpt-5')
    view.rerender(<MessageAssistant model="Auto">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).toBeNull()
    expect(view.container.textContent).not.toContain('Auto')
  })

  it('submits the entered downvote reason through the supplied persistent callback', () => {
    const submitted: Array<{ verdict: string; reason?: string }> = []
    const closed: string[] = []
    const view = render(<MessageAssistant feedback={{ verdict: 'down', busy: false,
      onSubmit: (verdict, reason) => submitted.push({ verdict, reason }),
      onClose: () => closed.push('closed'),
    }}>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: '  Missing source  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(submitted).toEqual([{ verdict: 'down', reason: 'Missing source' }])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel feedback' }))
    expect(closed).toEqual(['closed'])
  })

  it('exposes vote controls only when the caller supplies persistent actions', () => {
    const calls: string[] = []
    const view = render(<MessageAssistant onFeedbackUp={() => calls.push('up')}
      onFeedbackDown={() => calls.push('down')} feedbackVerdict="up">Saved answer</MessageAssistant>)
    const up = screen.getByRole('button', { name: 'Mark response helpful' })
    const down = screen.getByRole('button', { name: 'Mark response unhelpful' })
    expect(up.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(up)
    fireEvent.click(down)
    expect(calls).toEqual(['up', 'down'])
    view.rerender(<MessageAssistant feedbackBusy onFeedbackUp={() => calls.push('duplicate')}>Saved answer</MessageAssistant>)
    expect(screen.getByRole('button', { name: 'Mark response helpful' }).hasAttribute('disabled')).toBe(true)
    view.rerender(<MessageAssistant>Saved answer</MessageAssistant>)
    expect(screen.queryByRole('button', { name: 'Mark response helpful' })).toBeNull()
  })

  it('shows server error and blocks a duplicate submit while saving', () => {
    const submitted: string[] = []
    const view = render(<MessageAssistant onFeedbackUp={() => submitted.push('up')} feedback={{ verdict: 'down', busy: true, error: 'Feedback save failed',
      onSubmit: () => submitted.push('sent'), onClose: () => submitted.push('closed'),
    }}>Saved answer</MessageAssistant>)
    expect(screen.getByRole('alert').textContent).toBe('Feedback save failed')
    expect(screen.getByText('Saving feedback…')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Cancel feedback' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Mark response helpful' }).hasAttribute('disabled')).toBe(true)
    expect(submitted).toEqual([])
    view.rerender(<MessageAssistant>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).toBeNull()
  })

  it('submits a downvote with no invented reason when the note is blank', () => {
    const reasons: Array<string | undefined> = []
    render(<MessageAssistant feedback={{ verdict: 'down', busy: false,
      onSubmit: (_verdict, reason) => reasons.push(reason), onClose: () => {},
    }}>Answer</MessageAssistant>)
    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(reasons).toEqual([undefined])
  })
})

describe('persisted assistant file changes', () => {
  it('renders an exact applied diff and opens the recorded path without review actions', () => {
    const opened: string[] = []
    const view = render(<MessageAssistant fileChanges={[{ path: 'src/account.ts', before: 'one\nold\nshared\n', after: 'one\nnew\nshared\n' }]}
      onOpenFile={path => opened.push(path)}>Updated the account</MessageAssistant>)
    const tree = view.container.querySelector('[data-slot="file-tree"]')
    expect(tree?.textContent).toContain('1 files changed')
    expect(tree?.textContent).toContain('+1')
    expect(tree?.textContent).toContain('−1')
    fireEvent.click(screen.getByRole('button', { name: /src\/account.ts/ }))
    expect(opened).toEqual(['src/account.ts'])
    fireEvent.click(screen.getByText('File changes · src/account.ts'))
    const diff = view.container.querySelector('[data-slot="reviewable-diff"]')
    expect(diff?.textContent).toContain('Applied')
    expect(diff?.textContent).toContain('old')
    expect(diff?.textContent).toContain('new')
    expect(diff?.textContent).toContain('shared')
    expect(diff?.textContent).not.toContain('kept')
    expect(screen.queryByRole('button', { name: /Keep|Discard|Apply/ })).toBeNull()
  })

  it('counts actual file creation and deletion lines without inventing context', () => {
    const view = render(<MessageAssistant fileChanges={[
      { path: 'src/new.ts', before: '', after: 'first\nsecond\n' },
      { path: 'src/old.ts', before: 'removed\n', after: '' },
    ]}>Applied changes</MessageAssistant>)
    const tree = view.container.querySelector('[data-slot="file-tree"]')
    expect(tree?.textContent).toContain('2 files changed')
    expect(tree?.textContent).toContain('+2')
    expect(tree?.textContent).toContain('−1')
    expect(view.container.querySelectorAll('[data-slot="reviewable-diff"]')).toHaveLength(2)
    expect(view.container.textContent).toContain('@@ -0,0 +1,2 @@')
    expect(view.container.textContent).toContain('@@ -1,1 +0,0 @@')
    expect(tree?.querySelectorAll('button')).toHaveLength(0)
  })

  it('shows a truncated snapshot path without diff lines or fabricated counts', () => {
    const view = render(<MessageAssistant fileChanges={[
      { path: 'src/complete.ts', before: 'old\n', after: 'new\n' },
      { path: 'src/truncated.ts', before: 'begin\n… [truncated]', after: 'end\n… [truncated]' },
    ]}>Updated two files</MessageAssistant>)
    const tree = view.container.querySelector('[data-slot="file-tree"]')
    expect(tree?.textContent).toContain('src/truncated.ts')
    expect(tree?.firstElementChild?.textContent).not.toContain('+1')
    expect(tree?.firstElementChild?.textContent).not.toContain('−1')
    expect(view.container.querySelectorAll('[data-slot="reviewable-diff"]')).toHaveLength(1)
    expect(view.container.querySelector('summary')?.textContent).toContain('src/complete.ts')
    expect(view.container.querySelector('summary')?.textContent).not.toContain('src/truncated.ts')
  })

  it('keeps an oversized complete snapshot as a path without guessing line changes', () => {
    const manyLines = 'same\n'.repeat(2_001)
    const view = render(<MessageAssistant fileChanges={[{ path: 'src/large.ts', before: manyLines, after: manyLines + 'end\n' }]}>Updated file</MessageAssistant>)
    const tree = view.container.querySelector('[data-slot="file-tree"]')
    expect(tree?.textContent).toContain('src/large.ts')
    expect(tree?.textContent).not.toContain('+')
    expect(view.container.querySelector('[data-slot="reviewable-diff"]')).toBeNull()
  })

  it('compares complete snapshots even when neither ends with a newline', () => {
    const view = render(<MessageAssistant fileChanges={[{
      path: 'src/plain.txt', before: 'same\nold', after: 'same\nnew',
    }]}>Updated file</MessageAssistant>)
    const diff = view.container.querySelector('[data-slot="reviewable-diff"]')
    expect(diff?.textContent).toContain('same')
    expect(diff?.textContent).toContain('old')
    expect(diff?.textContent).toContain('new')
    expect(view.container.querySelector('[data-slot="file-tree"]')?.textContent).toContain('+1')
  })

  it('does not claim line counts for an end-of-file newline change', () => {
    const view = render(<MessageAssistant fileChanges={[{
      path: 'src/eof.txt', before: 'same', after: 'same\n',
    }]}>Updated file</MessageAssistant>)
    const tree = view.container.querySelector('[data-slot="file-tree"]')
    expect(tree?.textContent).toContain('src/eof.txt')
    expect(tree?.textContent).not.toContain('+0')
    expect(tree?.textContent).not.toContain('−0')
    expect(view.container.querySelector('[data-slot="reviewable-diff"]')).toBeNull()
  })

  it('adds and removes the donor file surface as persisted history changes', () => {
    const view = render(<MessageAssistant>Streaming answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="file-tree"]')).toBeNull()
    view.rerender(<MessageAssistant fileChanges={[{ path: 'src/new.ts', before: '', after: 'saved\n' }]}>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="file-tree"]')?.textContent).toContain('src/new.ts')
    view.rerender(<MessageAssistant fileChanges={[]}>Another answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="file-tree"]')).toBeNull()
  })
})
