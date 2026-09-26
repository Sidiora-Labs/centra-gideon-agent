import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { FeedbackDialog } from './feedback-dialog'
import { StoppedRun } from './stopped-run'
import { DaySeparator, type DatedMessage } from './day-separator'
import { SpeakerIdentity, type SpeakerTurn } from './speaker-identity'
import { RegenerateMenu, type RegenerateOption } from './regenerate-menu'
import { ConfidenceMarker, type ConfidenceClaim } from './confidence-marker'

afterEach(cleanup)

describe('FeedbackDialog', () => {
  it('renders the donor reason, note, and submission controls with real callbacks', () => {
    const toggle = vi.fn()
    const noteChange = vi.fn()
    const submit = vi.fn()
    const view = render(<FeedbackDialog reasons={['Incorrect answer', 'Missing source']}
      selected={['Incorrect answer']} note="" sent={false} onToggleReason={toggle}
      onNoteChange={noteChange} onSubmit={submit} data-testid="feedback" />)
    const root = view.getByTestId('feedback')
    expect(root.dataset.slot).toBe('feedback-dialog')
    const incorrect = within(root).getByRole('button', { name: 'Incorrect answer' })
    const missing = within(root).getByRole('button', { name: 'Missing source' })
    expect(incorrect.getAttribute('aria-pressed')).toBe('true')
    expect(missing.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(incorrect)
    fireEvent.click(missing)
    expect(toggle.mock.calls).toEqual([['Incorrect answer'], ['Missing source']])
    fireEvent.change(within(root).getByRole('textbox', { name: 'Anything else?' }),
      { target: { value: 'Citation missing on the second claim' } })
    expect(noteChange).toHaveBeenCalledTimes(1)
    expect(noteChange).toHaveBeenCalledWith('Citation missing on the second claim')
    fireEvent.click(within(root).getByRole('button', { name: 'Send feedback' }))
    expect(submit).toHaveBeenCalledOnce()
  })

  it('accepts a note-only downvote and lets a saving wrapper remove submit', () => {
    const update = vi.fn()
    const send = vi.fn()
    const view = render(<FeedbackDialog reasons={[]} selected={[]} note="First note"
      sent={false} onNoteChange={update} onSubmit={send} />)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).not.toBeNull()
    expect(view.container.querySelectorAll('[aria-pressed]')).toHaveLength(0)
    const note = screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement
    expect(note.value).toBe('First note')
    fireEvent.change(note, { target: { value: 'Clarify the result' } })
    expect(update).toHaveBeenCalledTimes(1)
    expect(update).toHaveBeenCalledWith('Clarify the result')
    view.rerender(<fieldset disabled><FeedbackDialog reasons={[]} selected={[]}
      note="Clarify the result" sent={false} onNoteChange={update} /></fieldset>)
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Anything else?' }).matches(':disabled')).toBe(true)
    expect(send).not.toHaveBeenCalled()
  })

  it('shows static reasons and a read-only note when no actions are supplied', () => {
    const view = render(<FeedbackDialog reasons={['Other']} selected={['Other']}
      note="Saved explanation" sent={false} />)
    expect(screen.queryByRole('button', { name: 'Other' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    const reason = screen.getByText('Other')
    expect(reason.tagName).toBe('SPAN')
    expect(reason.dataset.selected).toBe('true')
    const note = screen.getByRole('textbox', { name: 'Anything else?' }) as HTMLTextAreaElement
    expect(note.readOnly).toBe(true)
    expect(view.container.querySelector('[role="status"]')).not.toBeNull()
  })

  it('announces only confirmed submission and removes editable content', () => {
    const submit = vi.fn()
    const view = render(<FeedbackDialog reasons={['Other']} selected={[]}
      note="" sent={false} onSubmit={submit} />)
    const status = view.container.querySelector('[role="status"]') as HTMLElement
    expect(status.textContent).not.toContain('Feedback sent')
    view.rerender(<FeedbackDialog reasons={['Other']} selected={['Other']}
      note="real note" sent onSubmit={submit} />)
    expect(status.textContent).toContain('Feedback sent.')
    expect(status.textContent).not.toMatch(/tune the model/i)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(submit).not.toHaveBeenCalled()
  })
})

describe('StoppedRun', () => {
  it('renders the actual partial words and supplied stop reason', () => {
    const view = render(<StoppedRun words={['Partial', 'answer']} reason="Stopped by user" />)
    const root = view.container.querySelector('[data-slot="stopped-run"]') as HTMLElement
    expect(root.textContent).toContain('Partial answer')
    expect(root.textContent).toContain('Stopped by user')
    expect(within(root).queryByRole('button')).toBeNull()
    expect(root.querySelectorAll('svg')).toHaveLength(1)
  })

  it('shows a persisted stop reason without repeating partial words when requested', () => {
    const words = ['Already', 'rendered', 'above']
    const view = render(<StoppedRun words={words} reason="Stopped by user" showWords={false} />)
    const root = view.container.querySelector('[data-slot="stopped-run"]') as HTMLElement
    expect(root.querySelector('p')).toBeNull()
    expect(root.textContent).toContain('Stopped by user')
    expect(root.textContent).not.toContain(words.join(' '))
    expect(within(root).queryByRole('button')).toBeNull()
    view.rerender(<StoppedRun words={words} reason="Stopped by user" />)
    expect(root.querySelector('p')?.textContent).toContain(words.join(' '))
    expect(root.querySelector('p span[aria-hidden]')).not.toBeNull()
    view.rerender(<StoppedRun words={words} reason="Stopped by user" showWords />)
    expect(root.querySelector('p')?.textContent).toContain(words.join(' '))
  })

  it('offers only the continuation action when continuation exists', () => {
    const continueRun = vi.fn()
    render(<StoppedRun words={['Draft']} reason="Interrupted" onContinue={continueRun} />)
    const button = screen.getByRole('button', { name: 'Continue' })
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull()
    fireEvent.click(button)
    expect(continueRun).toHaveBeenCalledOnce()
  })

  it('offers only discard when available and keeps callbacks distinct', () => {
    const discard = vi.fn()
    const continueRun = vi.fn()
    const view = render(<StoppedRun words={[]} reason="Limit reached"
      onDiscard={discard} onContinue={continueRun} />)
    expect(view.container.querySelector('[data-slot="stopped-run"] p')?.textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(discard).toHaveBeenCalledOnce()
    expect(continueRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(continueRun).toHaveBeenCalledOnce()
  })
})

describe('DaySeparator', () => {
  const messages: DatedMessage[] = [
    { id: 'm1', day: 'Today', time: '09:00', role: 'user', text: 'Morning' },
    { id: 'm2', day: 'Today', time: '09:01', role: 'assistant', text: 'Hello' },
    { id: 'm3', day: 'Yesterday', time: '17:45', role: 'user', text: 'Earlier' },
  ]

  it('groups consecutive messages by supplied day in chronological order', () => {
    const view = render(<DaySeparator messages={messages} data-testid="days" />)
    const root = view.getByTestId('days')
    expect(root.dataset.slot).toBe('day-separator')
    expect(within(root).getAllByText('Today')).toHaveLength(1)
    expect(within(root).getAllByText('Yesterday')).toHaveLength(1)
    expect(root.textContent?.indexOf('Morning')).toBeLessThan(root.textContent?.indexOf('Hello') ?? 0)
    expect(root.textContent?.indexOf('Hello')).toBeLessThan(root.textContent?.indexOf('Earlier') ?? 0)
    expect(within(root).getByText('09:00')).not.toBeNull()
    expect(within(root).getByText('17:45')).not.toBeNull()
  })

  it('starts a new divider when the same day returns later in supplied order', () => {
    const reordered = [...messages, { id: 'm4', day: 'Today', time: '18:00', role: 'assistant' as const, text: 'Return' }]
    render(<DaySeparator messages={reordered} />)
    expect(screen.getAllByText('Today')).toHaveLength(2)
    expect(screen.getByText('Return')).not.toBeNull()
    expect(screen.getByText('18:00')).not.toBeNull()
  })

  it('keeps user and assistant alignment distinct and handles an empty list', () => {
    const view = render(<DaySeparator messages={messages} className="custom-day" />)
    expect(view.container.firstElementChild?.classList.contains('custom-day')).toBe(true)
    expect(screen.getByText('Morning').parentElement?.className).toContain('flex-row-reverse')
    expect(screen.getByText('Hello').parentElement?.className).not.toContain('flex-row-reverse')
    view.rerender(<DaySeparator messages={[]} />)
    expect(view.container.querySelector('[data-slot="day-separator"]')?.children).toHaveLength(0)
  })
})

describe('SpeakerIdentity', () => {
  const turns: SpeakerTurn[] = [
    { id: 'u', kind: 'user', name: 'You', text: 'Can you check?' },
    { id: 'a', kind: 'agent', name: 'Gideon', detail: 'primary', text: 'Checking.' },
    { id: 's', kind: 'subagent', name: 'Research', detail: 'delegated', text: 'Found two sources.' },
    { id: 't', kind: 'tool', name: 'Search', text: '2 results' },
  ]

  it('renders role, identity, detail, and actual turn text for all four kinds', () => {
    const view = render(<SpeakerIdentity turns={turns} data-testid="speakers" />)
    const root = view.getByTestId('speakers')
    expect(root.dataset.slot).toBe('speaker-identity')
    for (const turn of turns) {
      expect(within(root).getByText(turn.name)).not.toBeNull()
      expect(within(root).getByText(turn.text)).not.toBeNull()
    }
    expect(within(root).getByText('primary')).not.toBeNull()
    expect(within(root).getByText('delegated')).not.toBeNull()
    expect(root.querySelectorAll('svg')).toHaveLength(4)
  })

  it('distinguishes delegated agents visually without inventing missing detail', () => {
    const view = render(<SpeakerIdentity turns={turns} />)
    const delegated = screen.getByText('Research').closest('div.flex') as HTMLElement
    expect(delegated?.previousElementSibling?.className).toContain('rounded-full')
    const user = screen.getByText('You').closest('div.flex') as HTMLElement
    expect(user?.previousElementSibling?.className).not.toContain('rounded-full')
    expect(view.container.textContent).not.toContain('undefined')
  })

  it('renders no invented speaker or message when no turns are supplied', () => {
    const view = render(<SpeakerIdentity turns={[]} className="custom-speaker" />)
    const root = view.container.querySelector('[data-slot="speaker-identity"]') as HTMLElement
    expect(root.classList.contains('custom-speaker')).toBe(true)
    expect(root.children).toHaveLength(0)
    expect(root.textContent).toBe('')
  })
})

describe('RegenerateMenu', () => {
  const options: RegenerateOption[] = [
    { id: 'standard', label: 'Try again', detail: 'same settings' },
    { id: 'alternate', label: 'Use alternate', detail: 'available option' },
  ]

  it('opens via caller state and passes the picked option id', () => {
    const change = vi.fn()
    const pick = vi.fn()
    const view = render(<RegenerateMenu options={options} open={false} currentId="standard"
      onOpenChange={change} onPick={pick} />)
    const toggle = screen.getByRole('button', { name: 'Regenerate response options' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: /Use alternate/ })).toBeNull()
    fireEvent.click(toggle)
    expect(change).toHaveBeenCalledTimes(1)
    expect(change).toHaveBeenCalledWith(true)
    view.rerender(<RegenerateMenu options={options} open currentId="standard"
      onOpenChange={change} onPick={pick} />)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('current')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Use alternate/ }))
    expect(pick).toHaveBeenCalledTimes(1)
    expect(pick).toHaveBeenCalledWith('alternate')
  })

  it('requests closing without silently changing its controlled open state', () => {
    const change = vi.fn()
    render(<RegenerateMenu options={options} open currentId="alternate" onOpenChange={change} />)
    const toggle = screen.getByRole('button', { name: 'Regenerate response options' })
    fireEvent.click(toggle)
    expect(change).toHaveBeenCalledTimes(1)
    expect(change).toHaveBeenCalledWith(false)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('current')).not.toBeNull()
  })

  it('shows static options without an actionable pick callback', () => {
    const view = render(<RegenerateMenu options={options} open currentId="standard" />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('Try again').parentElement?.tagName).toBe('DIV')
    expect(screen.getByText('Use alternate').parentElement?.tagName).toBe('DIV')
    expect(screen.getByText('available option')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="regenerate-menu"]')).not.toBeNull()
  })

  it('handles no available options and does not claim a model change', () => {
    const change = vi.fn()
    const view = render(<RegenerateMenu options={[]} open currentId=""
      onOpenChange={change} className="custom-regenerate" />)
    expect(view.container.firstElementChild?.classList.contains('custom-regenerate')).toBe(true)
    expect(screen.queryByText('current')).toBeNull()
    expect(screen.queryByRole('button', { name: /model/i })).toBeNull()
    expect(screen.getByRole('button', { name: 'Regenerate response options' })).not.toBeNull()
  })
})

describe('ConfidenceMarker', () => {
  const claims: ConfidenceClaim[] = [
    { id: 'c1', text: 'Documented fact', confidence: 'grounded', basis: 'project log' },
    { id: 'c2', text: 'Likely consequence', confidence: 'inferred', basis: 'available evidence' },
    { id: 'c3', text: 'Open question', confidence: 'uncertain', basis: 'no source yet' },
  ]

  it('shows only the hovered claim basis and reports focus changes to the caller', () => {
    const hover = vi.fn()
    const view = render(<ConfidenceMarker claims={claims} hoveredId="" onHover={hover} />)
    expect(view.container.querySelector('[data-slot="confidence-marker"]')).not.toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
    const grounded = screen.getByRole('button', { name: 'Documented fact' })
    fireEvent.mouseEnter(grounded)
    expect(hover).toHaveBeenCalledWith('c1')
    view.rerender(<ConfidenceMarker claims={claims} hoveredId="c1" onHover={hover} />)
    const status = screen.getByRole('status')
    expect(status.textContent).toContain('from a source · project log')
    expect(grounded.getAttribute('aria-describedby')).toBe(status.id)
    fireEvent.mouseLeave(grounded)
    expect(hover).toHaveBeenLastCalledWith('')
    fireEvent.focus(screen.getByRole('button', { name: 'Open question' }))
    expect(hover).toHaveBeenLastCalledWith('c3')
    fireEvent.blur(screen.getByRole('button', { name: 'Open question' }))
    expect(hover).toHaveBeenLastCalledWith('')
  })

  it('discloses inferred and uncertain basis without changing supplied claims', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="c2" onHover={() => {}} />)
    expect(screen.getByRole('status').textContent).toContain('inferred · available evidence')
    view.rerender(<ConfidenceMarker claims={claims} hoveredId="c3" onHover={() => {}} />)
    expect(screen.getByRole('status').textContent).toContain('unverified · no source yet')
    expect(claims.map((claim) => claim.confidence)).toEqual(['grounded', 'inferred', 'uncertain'])
  })

  it('renders static claims instead of inert buttons without a hover callback', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="" />)
    expect(screen.queryByRole('button')).toBeNull()
    for (const claim of claims) {
      const element = screen.getByText(claim.text)
      expect(element.tagName).toBe('SPAN')
      expect(element.className).toContain('underline')
    }
    expect(view.container.querySelectorAll('[aria-describedby]')).toHaveLength(0)
  })

  it('keeps supplied class names and avoids basis disclosure for unknown ids', () => {
    const view = render(<ConfidenceMarker claims={claims} hoveredId="missing"
      onHover={() => {}} className="custom-confidence" />)
    expect(view.container.firstElementChild?.classList.contains('custom-confidence')).toBe(true)
    expect(screen.queryByRole('status')).toBeNull()
    expect(view.container.querySelectorAll('[aria-describedby]')).toHaveLength(0)
    view.rerender(<ConfidenceMarker claims={[]} hoveredId="missing" />)
    expect(view.container.querySelector('[data-slot="confidence-marker"] p')?.textContent).toBe('')
  })
})
